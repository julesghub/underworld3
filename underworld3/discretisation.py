# from telnetlib import DM
# from this import d
from typing import Optional, Tuple, Union
import os
from xmlrpc.client import Boolean
import mpi4py
import numpy
import sympy
import sympy.vector
from petsc4py import PETSc
import underworld3 as uw

from underworld3.utilities import _api_tools
from underworld3.coordinates import CoordinateSystem, CoordinateSystemType
from underworld3.cython import petsc_discretisation


import underworld3.timing as timing
import weakref


@PETSc.Log.EventDecorator()
def _from_gmsh(filename, comm=None, cellSets=None, faceSets=None, vertexSets=None):
    """Read a Gmsh .msh file from `filename`.

    :kwarg comm: Optional communicator to build the mesh on (defaults to
        COMM_WORLD).
    """
    comm = comm or PETSc.COMM_WORLD
    # Create a read-only PETSc.Viewer
    # gmsh_viewer = PETSc.Viewer().create(comm=comm)
    # gmsh_viewer.setType("ascii")
    # gmsh_viewer.setFileMode("r")
    # gmsh_viewer.setFileName(filename)
    # gmsh_plex = PETSc.DMPlex().createGmsh(gmsh_viewer, comm=comm)

    # This is probably simpler
    gmsh_plex = PETSc.DMPlex().createFromFile(filename)

    # Extract Physical groups from the gmsh file

    ## NOTE: should we be doing this is parallel ??
    ## 1) Race conditions on gmsh files / locking etc
    ## 2) Why not pass in these collections from the gmsh generator and process accordingly

    import gmsh

    gmsh.initialize()
    gmsh.model.add("Model")
    gmsh.open(filename)

    ## What about cells and vertices ?

    physical_groups = {}
    for dim, tag in gmsh.model.get_physical_groups():

        name = gmsh.model.get_physical_name(dim, tag)

        physical_groups[name] = tag

        gmsh_plex.createLabel(name)
        label = gmsh_plex.getLabel(name)

        for elem in ["Face Sets"]:
            indexSet = gmsh_plex.getStratumIS(elem, tag)
            if indexSet:
                label.insertIS(indexSet, 1)
            indexSet.destroy()

    # cell sets / face sets / vertex sets by numerical tag (by hand for the case where  gmsh has no physical groups)

    if cellSets is not None:
        for cellSet in cellSets:
            label_name = cellSet["name"]
            label_id = cellSet["id"]

            gmsh_plex.createLabel(label_name)
            label = gmsh_plex.getLabel(label_name)
            indexSet = gmsh_plex.getStratumIS("Cell Sets", label_id)
            if indexSet:
                label.insertIS(indexSet, 1)
            else:
                gmsh_plex.removeLabel(label_name)
            indexSet.destroy()

    if faceSets is not None:
        for faceSet in faceSets:
            label_name = faceSet["name"]
            label_id = faceSet["id"]

            gmsh_plex.createLabel(label_name)
            label = gmsh_plex.getLabel(label_name)
            indexSet = gmsh_plex.getStratumIS("Face Sets", label_id)
            if indexSet:
                label.insertIS(indexSet, 1)
            else:
                gmsh_plex.removeLabel(label_name)
            indexSet.destroy()

    if vertexSets is not None:
        for vertexSet in vertexSets:
            label_name = vertexSet["name"]
            label_id = vertexSet["id"]

            gmsh_plex.createLabel(label_name)
            label = gmsh_plex.getLabel(label_name)
            indexSet = gmsh_plex.getStratumIS("Vertex Sets", label_id)
            if indexSet:
                label.insertIS(indexSet, 1)
            else:
                gmsh_plex.removeLabel(label_name)
            indexSet.destroy()

    gmsh.finalize()

    return gmsh_plex


class Mesh(_api_tools.Stateful):

    mesh_instances = 0

    @timing.routine_timer_decorator
    def __init__(
        self,
        plex_or_meshfile,
        degree=1,
        simplex=True,
        coordinate_system_type=None,
        qdegree=2,
        cellSets=None,
        vertexSets=None,
        faceSets=None,
        filename=None,
        *args,
        **kwargs,
    ):

        if isinstance(plex_or_meshfile, PETSc.DMPlex):
            name = "plexmesh"
            self.dm = plex_or_meshfile
        else:
            comm = kwargs.get("comm", PETSc.COMM_WORLD)
            name = plex_or_meshfile
            basename, ext = os.path.splitext(plex_or_meshfile)

            # Note: should be able to handle a .geo as well on this pathway
            if ext.lower() == ".msh":
                self.dm = _from_gmsh(
                    plex_or_meshfile, comm, cellSets, faceSets, vertexSets
                )
            else:
                raise RuntimeError(
                    "Mesh file %s has unknown format '%s'."
                    % (plex_or_meshfile, ext[1:])
                )

        self.filename = filename
        self.dm.distribute()

        Mesh.mesh_instances += 1

        # Set sympy constructs. First a generic, symbolic, Cartesian coordinate system
        from sympy.vector import CoordSys3D

        # A unique set of vectors / names for each mesh instance
        self._N = CoordSys3D(f"N{Mesh.mesh_instances}")

        self._N.x._latex_form = r"\mathrm{\xi_0}"
        self._N.y._latex_form = r"\mathrm{\xi_1}"
        self._N.z._latex_form = r"\mathrm{\xi_2}"
        self._N.i._latex_form = r"\mathbf{\hat{\mathbf{e}}_0}"
        self._N.j._latex_form = r"\mathbf{\hat{\mathbf{e}}_1}"
        self._N.k._latex_form = r"\mathbf{\hat{\mathbf{e}}_2}"

        # Now add the appropriate coordinate system for the mesh's natural geometry
        # This step will usually over-write the defaults we just defined
        self._CoordinateSystem = CoordinateSystem(self, coordinate_system_type)

        # Tidy some of this printing without changing the
        # underlying vector names (as these are part of the code generation system)

        try:
            self.isSimplex = self.dm.isSimplex()
        except:
            self.isSimplex = simplex

        # Use grid hashing for point location
        options = PETSc.Options()
        options["dm_plex_hash_location"] = None
        self.dm.setFromOptions()

        self._vars = weakref.WeakValueDictionary()

        # a list of equation systems that will
        # need to be rebuilt if the mesh coordinates change

        self._equation_systems_register = []

        self._accessed = False
        self._quadrature = False
        self._stale_lvec = True
        self._lvec = None
        self.petsc_fe = None

        self.degree = degree
        self.qdegree = qdegree

        self.nuke_coords_and_rebuild()

        # A private work array used in the stats routines.
        # This is defined now since we cannot make a new one
        # once the init phase of uw3 is complete.

        self._work_MeshVar = MeshVariable("work_array_1", self, 1, degree=2)

        # This looks a bit strange, but we'd like to
        # put these mesh-dependent vector calculus functions
        # and mesh-based tensor manipulation routines
        # in a bundle to avoid the mesh being required as an argument
        # since this could lead to things going out of sync

        if (
            self.CoordinateSystem.coordinate_type
            == CoordinateSystemType.CYLINDRICAL2D_NATIVE
            or self.CoordinateSystem.coordinate_type
            == CoordinateSystemType.CYLINDRICAL3D_NATIVE
        ):
            self.vector = uw.maths.vector_calculus_cylindrical(mesh=self)
        elif (
            self.CoordinateSystem.coordinate_type
            == CoordinateSystemType.SPHERICAL_NATIVE
        ):
            self.vector = uw.maths.vector_calculus_spherical_lonlat(
                mesh=self
            )  ## Not yet complete or tested
        else:
            self.vector = uw.maths.vector_calculus(mesh=self)

        super().__init__()

    @property
    def dim(self) -> int:
        """
        The mesh dimensionality.
        """
        return self.dm.getDimension()

    @property
    def cdim(self) -> int:
        """
        The mesh dimensionality.
        """
        return self.dm.getCoordinateDim()

    def nuke_coords_and_rebuild(self):

        # This is a reversion to the old version (3.15 compatible which seems to work in 3.16 too)

        self._coord_array = {}

        # let's go ahead and do an initial projection from linear (the default)
        # to linear. this really is a nothing operation, but a
        # side effect of this operation is that coordinate DM DMField is
        # converted to the required `PetscFE` type. this may become necessary
        # later where we call the interpolation routines to project from the linear
        # mesh coordinates to other mesh coordinates.

        options = PETSc.Options()
        options.setValue(
            "meshproj_{}_petscspace_degree".format(self.mesh_instances), self.degree
        )

        self.petsc_fe = PETSc.FE().createDefault(
            self.dim,
            self.cdim,
            self.isSimplex,
            self.qdegree,
            "meshproj_{}_".format(self.mesh_instances),
            PETSc.COMM_WORLD,
        )

        if self.degree != 1:

            # We have to be careful as a projection onto an equivalent PETScFE can cause problematic
            # issues with petsc that we see in parallel - in which case there is a fallback, pass no
            # PETScFE and let PETSc decide. Note that the petsc4py wrapped version does not allow this
            # (but it should !)

            self.dm.projectCoordinates(self.petsc_fe)

        else:

            uw.cython.petsc_discretisation.petsc_dm_project_coordinates(self.dm)

        # now set copy of this array into dictionary

        arr = self.dm.getCoordinatesLocal().array

        key = (
            self.isSimplex,
            self.degree,
            True,
        )  # True here assumes continuous basis for coordinates ...

        self._coord_array[key] = arr.reshape(-1, self.cdim).copy()

        # self._centroids = self._get_coords_for_basis(0, True)
        self._centroids = self._get_mesh_centroids()

        # invalidate the cell-search k-d tree and the mesh centroid data
        self._index = None

        return

    @timing.routine_timer_decorator
    def update_lvec(self):
        """
        This method creates and/or updates the mesh variable local vector.
        If the local vector is already up to date, this method will do nothing.
        """

        if self._stale_lvec:
            if not self._lvec:
                # self.dm.clearDS()
                # self.dm.createDS()
                # create the local vector (memory chunk) and attach to original dm
                self._lvec = self.dm.createLocalVec()
            # push avar arrays into the parent dm array
            a_global = self.dm.getGlobalVec()
            names, isets, dms = self.dm.createFieldDecomposition()

            with self.access():
                # traverse subdms, taking user generated data in the subdm
                # local vec, pushing it into a global sub vec
                for var, subiset, subdm in zip(self.vars.values(), isets, dms):
                    lvec = var.vec
                    subvec = a_global.getSubVector(subiset)
                    subdm.localToGlobal(lvec, subvec, addv=False)
                    a_global.restoreSubVector(subiset, subvec)

            self.dm.globalToLocal(a_global, self._lvec)
            self.dm.restoreGlobalVec(a_global)
            self._stale_lvec = False

    @property
    def lvec(self) -> PETSc.Vec:
        """
        Returns a local Petsc vector containing the flattened array
        of all the mesh variables.
        """
        if self._stale_lvec:
            raise RuntimeError(
                "Mesh `lvec` needs to be updated using the update_lvec()` method."
            )
        return self._lvec

    def __del__(self):
        if hasattr(self, "_lvec") and self._lvec:
            self._lvec.destroy()

    def deform_mesh(self, new_coords: numpy.ndarray):
        """
        This method will update the mesh coordinates and reset any cached coordinates in
        the mesh and in equation systems that are registered on the mesh.

        The coord array that is passed in should match the shape of self.data
        """

        coord_vec = self.dm.getCoordinatesLocal()
        coords = coord_vec.array.reshape(-1, self.cdim)
        coords[...] = new_coords[...]

        self.dm.setCoordinatesLocal(coord_vec)
        self.nuke_coords_and_rebuild()

        for eq_system in self._equation_systems_register:
            eq_system._rebuild_after_mesh_update()

        return

    def access(self, *writeable_vars: "MeshVariable"):
        """
        This context manager makes the underlying mesh variables data available to
        the user. The data should be accessed via the variables `data` handle.

        As default, all data is read-only. To enable writeable data, the user should
        specify which variable they wish to modify.

        Parameters
        ----------
        writeable_vars
            The variables for which data write access is required.

        Example
        -------
        >>> import underworld3 as uw
        >>> someMesh = uw.discretisation.FeMesh_Cartesian()
        >>> with someMesh.deform_mesh():
        ...     someMesh.data[0] = [0.1,0.1]
        >>> someMesh.data[0]
        array([ 0.1,  0.1])
        """

        import time

        timing._incrementDepth()
        stime = time.time()

        self._accessed = True
        deaccess_list = []
        for var in self.vars.values():
            # if already accessed within higher level context manager, continue.
            if var._is_accessed == True:
                continue

            # set flag so variable status can be known elsewhere
            var._is_accessed = True
            # add to de-access list to rewind this later
            deaccess_list.append(var)

            # create & set vec
            var._set_vec(available=True)

            # grab numpy object, setting read only if necessary
            var._data = var.vec.array.reshape(-1, var.num_components)

            if var not in writeable_vars:
                var._old_data_flag = var._data.flags.writeable
                var._data.flags.writeable = False
            else:
                # increment variable state
                var._increment()

        class exit_manager:
            def __init__(self, mesh):
                self.mesh = mesh

            def __enter__(self):
                pass

            def __exit__(self, *args):
                for var in self.mesh.vars.values():
                    # only de-access variables we have set access for.
                    if var not in deaccess_list:
                        continue
                    # set this back, although possibly not required.
                    if var not in writeable_vars:
                        var._data.flags.writeable = var._old_data_flag
                    # perform sync for any modified vars.

                    if var in writeable_vars:
                        indexset, subdm = self.mesh.dm.createSubDM(var.field_id)

                        # sync ghost values
                        subdm.localToGlobal(var.vec, var._gvec, addv=False)
                        subdm.globalToLocal(var._gvec, var.vec, addv=False)

                        # subdm.destroy()
                        self.mesh._stale_lvec = True

                    var._data = None
                    var._set_vec(available=False)
                    var._is_accessed = False

                timing._decrementDepth()
                timing.log_result(time.time() - stime, "Mesh.access", 1)

        return exit_manager(self)

    @property
    def N(self) -> sympy.vector.CoordSys3D:
        """
        The mesh coordinate system.
        """
        return self._N

    @property
    def X(self) -> sympy.Matrix:
        return self._CoordinateSystem.X

    @property
    def CoordinateSystem(self) -> CoordinateSystem:
        return self._CoordinateSystem

    @property
    def r(self) -> Tuple[sympy.vector.BaseScalar]:
        """
        The tuple of base scalar objects (N.x,N.y,N.z) for the mesh.
        """
        return self._N.base_scalars()[0 : self.cdim]

    @property
    def rvec(self) -> sympy.vector.Vector:
        """
        The r vector, `r = N.x*N.i + N.y*N.j [+ N.z*N.k]`.
        """
        N = self.N

        r_vec = sympy.vector.Vector.zero

        N_s = N.base_scalars()
        N_v = N.base_vectors()
        for i in range(self.cdim):
            r_vec += N_s[i] * N_v[i]

        return r_vec

    @property
    def data(self) -> numpy.ndarray:
        """
        The array of mesh element vertex coordinates.
        """
        # get flat array
        arr = self.dm.getCoordinatesLocal().array
        return arr.reshape(-1, self.cdim)

    @timing.routine_timer_decorator
    def save(self, filename: str, index: Optional[int] = None):
        """
        Save mesh data to the specified hdf5 file.

        Users will generally create this file, and then
        append mesh variable data to it via the variable
        `save` method.

        Parameters
        ----------
        filename :
            The filename for the mesh checkpoint file.
        index :
            Not yet implemented. An optional index which might
            correspond to the timestep (for example).

        """
        viewer = PETSc.ViewerHDF5().create(filename, "w", comm=PETSc.COMM_WORLD)
        if index:
            raise RuntimeError("Recording `index` not currently supported")
            ## JM:To enable timestep recording, the following needs to be called.
            ## I'm unsure if the corresponding xdmf functionality is enabled via
            ## the PETSc xdmf script.
            # viewer.pushTimestepping(viewer)
            # viewer.setTimestep(index)
        viewer(self.dm)

    def vtk(self, filename: str):
        """
        Save mesh to the specified file
        """

        viewer = PETSc.Viewer().createVTK(filename, "w", comm=PETSc.COMM_WORLD)
        viewer(self.dm)

    def generate_xdmf(self, filename: str):
        """
        This method generates an xdmf schema for the specified file.

        The filename of the generated file will be the same as the hdf5 file
        but with the `xmf` extension.

        Parameters
        ----------
        filename :
            File name of the checkpointed hdf5 file for which the
            xdmf schema will be written.
        """
        from underworld3.utilities.petsc_gen_xdmf import generateXdmf

        generateXdmf(filename)

    @property
    def vars(self):
        """
        A list of variables recorded on the mesh.
        """
        return self._vars

    def _get_coords_for_var(self, var):
        """
        This function returns the vertex array for the
        provided variable. If the array does not already exist,
        it is first created and then returned.
        """
        key = (self.isSimplex, var.degree, var.continuous)

        # if array already created, return.
        if key in self._coord_array:
            return self._coord_array[key]
        else:
            self._coord_array[key] = self._get_coords_for_basis(
                var.degree, var.continuous
            )
            return self._coord_array[key]

    def _get_coords_for_basis(self, degree, continuous):
        """
        This function returns the vertex array for the
        provided variable. If the array does not already exist,
        it is first created and then returned.
        """

        dmold = self.dm.getCoordinateDM()
        dmold.createDS()

        dmnew = dmold.clone()

        options = PETSc.Options()
        options["coordinterp_petscspace_degree"] = degree
        options["coordinterp_petscdualspace_lagrange_continuity"] = continuous
        options["coordinterp_petscdualspace_lagrange_node_endpoints"] = False

        dmfe = PETSc.FE().createDefault(
            self.dim,
            self.cdim,
            self.isSimplex,
            self.qdegree,
            "coordinterp_",
            PETSc.COMM_WORLD,
        )

        dmnew.setField(0, dmfe)
        dmnew.createDS()

        matInterp, vecScale = dmold.createInterpolation(dmnew)
        coordsOld = self.dm.getCoordinates()
        coordsNewL = dmnew.getLocalVec()
        coordsNewG = matInterp * coordsOld
        dmnew.globalToLocal(coordsNewG, coordsNewL)

        arr = coordsNewL.array
        arrcopy = arr.reshape(-1, self.cdim).copy()

        return arrcopy

    @timing.routine_timer_decorator
    def get_closest_cells(self, coords: numpy.ndarray) -> numpy.ndarray:
        """
        This method uses a kd-tree algorithm to find the closest
        cells to the provided coords. For a regular mesh, this should
        be exactly the owning cell, but if the mesh is deformed, this
        is not guaranteed.

        Parameters:
        -----------
        coords:
            An array of the coordinates for which we wish to determine the
            closest cells. This should be a 2-dimensional array of
            shape (n_coords,dim).

        Returns:
        --------
        closest_cells:
            An array of indices representing the cells closest to the provided
            coordinates. This will be a 1-dimensional array of
            shape (n_coords).
        """
        # Create index if required
        if not self._index:
            from underworld3.swarm import Swarm, SwarmPICLayout

            # Create a temp swarm which we'll use to populate particles
            # at gauss points. These will then be used as basis for
            # kd-tree indexing back to owning cells.
            tempSwarm = Swarm(self)
            # 4^dim pop is used. This number may need to be considered
            # more carefully, or possibly should be coded to be set dynamically.
            tempSwarm.populate(fill_param=4, layout=SwarmPICLayout.GAUSS)
            with tempSwarm.access():
                # Build index on particle coords
                self._indexCoords = tempSwarm.particle_coordinates.data.copy()
                self._index = uw.kdtree.KDTree(self._indexCoords)
                self._index.build_index()
                # Grab mapping back to cell_ids.
                # Note that this is the numpy array that we eventually return from this
                # method. As such, we take measures to ensure that we use `numpy.int64` here
                # because we cast from this type in  `_function.evaluate` to construct
                # the PETSc cell-sf datasets, and if instead a `numpy.int32` is used it
                # will cause bugs that are difficult to find.
                self._indexMap = numpy.array(
                    tempSwarm.particle_cellid.data[:, 0], dtype=numpy.int64
                )

        closest_points, dist, found = self._index.find_closest_point(coords)

        if not numpy.allclose(found, True):
            raise RuntimeError(
                "An error was encountered attempting to find the closest cells to the provided coordinates."
            )

        return self._indexMap[closest_points]

    def _get_mesh_centroids(self):
        """
        Obtain and cache the mesh centroids using underworld swarm technology.
        This routine is called when the mesh is built / rebuilt
        """

        from underworld3.swarm import Swarm, SwarmPICLayout

        tempSwarm = Swarm(self)
        tempSwarm.populate(fill_param=1, layout=SwarmPICLayout.GAUSS)

        with tempSwarm.access():
            # Build index on particle coords
            centroids = tempSwarm.data.copy()

        return centroids

    def get_min_radius(self) -> float:
        """
        This method returns the minimum distance from any cell centroid to a face.
        It wraps to the PETSc `DMPlexGetMinRadius` routine.
        """

        ## Note: The petsc4py version of DMPlexComputeGeometryFVM does not compute all cells and
        ## does not obtain the minimum radius for the mesh.

        from underworld3.cython.petsc_discretisation import petsc_fvm_get_min_radius

        if (not hasattr(self, "_min_radius")) or (self._min_radius == None):
            self._min_radius = petsc_fvm_get_min_radius(self)

        return self._min_radius

    # def get_boundary_subdm(self) -> PETSc.DM:
    #     """
    #     This method returns the boundary subdm that wraps DMPlexCreateSubmesh
    #     """
    #     from underworld3.petsc_discretisation import petsc_create_surface_submesh
    #     return petsc_create_surface_submesh(self, "Boundary", 666, )

    def stats(self, uw_function):
        """
        Returns various norms on the mesh for the provided function.
          - size
          - mean
          - min
          - max
          - sum
          - L2 norm
          - rms

          NOTE: this currently assumes scalar variables !
        """

        #       This uses a private work MeshVariable and the various norms defined there but
        #       could either be simplified to just use petsc vectors, or extended to
        #       compute integrals over the elements which is in line with uw1 and uw2

        from petsc4py.PETSc import NormType

        tmp = self._work_MeshVar

        with self.access(tmp):
            tmp.data[...] = uw.function.evaluate(uw_function, tmp.coords).reshape(-1, 1)

        vsize = self._work_MeshVar._gvec.getSize()
        vmean = tmp.mean()
        vmax = tmp.max()[1]
        vmin = tmp.min()[1]
        vsum = tmp.sum()
        vnorm2 = tmp.norm(NormType.NORM_2)
        vrms = vnorm2 / numpy.sqrt(vsize)

        return vsize, vmean, vmin, vmax, vsum, vnorm2, vrms

        ## Here we check the existence of the meshVariable and so on before defining a new one
        ## (and potentially losing the handle to the old one)


def MeshVariable(
    varname: Union[str, list],
    mesh: "underworld.mesh.Mesh",
    num_components: int,
    vtype: Optional["underworld.VarType"] = None,
    degree: int = 1,
    continuous: bool = True,
):

    """
    The MeshVariable class generates a variable supported by a finite element mesh and the
    underlying sympy representation that makes it possible to construct expressions that
    depend on the values of the MeshVariable.

    To set / read nodal values, use the numpy interface via the 'data' property.

    Parameters
    ----------
    name :
        A textual name for this variable.
    mesh :
        The supporting underworld mesh.
    num_components :
        The number of components this variable has.
        For example, scalars will have `num_components=1`,
        while a 2d vector would have `num_components=2`.
    vtype :
        Optional. The underworld variable type for this variable.
        If not defined it will be inferred from `num_components`
        if possible.
    degree :
        The polynomial degree for this variable.

    """

    if isinstance(varname, list):
        name = varname[0] + R"+ \dots"
    else:
        name = varname

    if mesh._accessed:
        print(
            "It is not possible to add new variables to a mesh after existing variables have been accessed"
        )
        print("Variable {name} has NOT been added")
        return

    ## Smash if already defined (we should check this BEFORE the old meshVariable object is destroyed)

    if name in mesh.vars.keys():
        print(f"Variable with name {name} already exists on the mesh - Skipping.")
        return mesh.vars[name]

    return _MeshVariable(varname, mesh, num_components, vtype, degree, continuous)


class _MeshVariable(_api_tools.Stateful):
    @timing.routine_timer_decorator
    def __init__(
        self,
        varname: Union[str, list],
        mesh: "underworld.mesh.Mesh",
        num_components: int,
        vtype: Optional["underworld.VarType"] = None,
        degree: int = 1,
        continuous: bool = True,
    ):
        """
        The MeshVariable class generates a variable supported by a finite element mesh and the
        underlying sympy representation that makes it possible to construct expressions that
        depend on the values of the MeshVariable.

        To set / read nodal values, use the numpy interface via the 'data' property.

        Parameters
        ----------
        name :
            A textual name for this variable.
        mesh :
            The supporting underworld mesh.
        num_components :
            The number of components this variable has.
            For example, scalars will have `num_components=1`,
            while a 2d vector would have `num_components=2`.
        vtype :
            Optional. The underworld variable type for this variable.
            If not defined it will be inferred from `num_components`
            if possible.
        degree :
            The polynomial degree for this variable.

        """

        if isinstance(varname, list):
            name = varname[0] + R"+ \dots"
        else:
            name = varname

        self._lvec = None
        self._gvec = None
        self._data = None
        self._is_accessed = False
        self._available = False

        self.name = name

        import re

        self.clean_name = re.sub(r"[^a-zA-Z0-9]", "", name)

        if vtype == None:
            if num_components == 1:
                vtype = uw.VarType.SCALAR
            elif num_components == mesh.dim:
                vtype = uw.VarType.VECTOR
            else:
                raise ValueError(
                    "Unable to infer variable type from `num_components`. Please explicitly set the `vtype` parameter."
                )

        if not isinstance(vtype, uw.VarType):
            raise ValueError(
                "'vtype' must be an instance of 'Variable_Type', for example `underworld.VarType.SCALAR`."
            )

        self.vtype = vtype
        self.mesh = mesh
        self.num_components = num_components
        self.degree = degree
        self.continuous = continuous

        options = PETSc.Options()
        name0 = self.clean_name
        options.setValue(f"{name0}_petscspace_degree", degree)
        options.setValue(f"{name0}_petscdualspace_lagrange_continuity", continuous)
        options.setValue(
            f"{name0}_petscdualspace_lagrange_node_endpoints", False
        )  # only active if discontinuous

        dim = self.mesh.dm.getDimension()

        self.petsc_fe = PETSc.FE().createDefault(
            dim,
            num_components,
            self.mesh.isSimplex,
            self.mesh.qdegree,
            name0 + "_",
            PETSc.COMM_WORLD,
        )

        self.field_id = self.mesh.dm.getNumFields()
        self.mesh.dm.setField(self.field_id, self.petsc_fe)

        # self.mesh.dm.clearDS()
        # self.mesh.dm.createDS()

        # create associated sympy function
        from underworld3.function import UnderworldFunction

        if vtype == uw.VarType.SCALAR:
            self._sym = sympy.Matrix.zeros(1, 1)
            self._sym[0] = UnderworldFunction(name, self, vtype)(*self.mesh.r)
            self._sym[0].mesh = self.mesh

            self._ijk = self._sym[0]

        elif vtype == uw.VarType.VECTOR:
            self._sym = sympy.Matrix.zeros(1, num_components)

            # Matrix form (any number of components)
            for comp in range(num_components):
                self._sym[0, comp] = UnderworldFunction(name, self, vtype, comp)(
                    *self.mesh.r
                )
                self._sym[0, comp].mesh = self.mesh

            # Spatial vector form (2 vectors and 3 vectors according to mesh dim)
            if num_components == mesh.dim:
                self._ijk = sympy.vector.matrix_to_vector(self._sym, self.mesh.N)
                # self.mesh.vector.to_vector(self._sym)

        elif (
            vtype == uw.VarType.COMPOSITE
        ):  # This is just to allow full control over the names of the components
            self._sym = sympy.Matrix.zeros(1, num_components)
            if isinstance(varname, list):
                if len(varname) == num_components:
                    for comp in range(num_components):
                        self._sym[0, comp] = UnderworldFunction(
                            varname[comp], self, vtype, comp
                        )(*self.mesh.r)
                        self._sym[0, comp].mesh = self.mesh

                else:
                    raise RuntimeError(
                        "Please supply a list of names for all components of this vector"
                    )
            else:
                for comp in range(num_components):
                    self._sym[0, comp] = UnderworldFunction(name, self, vtype, comp)(
                        *self.mesh.r
                    )

        super().__init__()

        self.mesh.vars[name] = self

        self.mesh.dm.clearDS()
        self.mesh.dm.createDS()

        return

    @timing.routine_timer_decorator
    def save(
        self, filename: str, name: Optional[str] = None, index: Optional[int] = None
    ):
        """
        Append variable data to the specified mesh hdf5
        data file. The file must already exist.

        Parameters
        ----------
        filename :
            The filename of the mesh checkpoint file. It
            must already exist.
        name :
            Textual name for dataset. In particular, this
            will be used for XDMF generation. If not
            provided, the variable name will be used.
        index :
            Not currently supported. An optional index which
            might correspond to the timestep (for example).
        """
        viewer = PETSc.ViewerHDF5().create(filename, "a", comm=PETSc.COMM_WORLD)
        if index:
            raise RuntimeError("Recording `index` not currently supported")
            ## JM:To enable timestep recording, the following needs to be called.
            ## I'm unsure if the corresponding xdmf functionality is enabled via
            ## the PETSc xdmf script.
            # PetscViewerHDF5PushTimestepping(cviewer)
            # viewer.setTimestep(index)

        if name:
            oldname = self._gvec.getName()
            self._gvec.setName(name)
        viewer(self._gvec)
        if name:
            self._gvec.setName(oldname)

    @property
    def fn(self) -> sympy.Basic:
        """
        The handle to the function view of this variable.
        """
        return self._ijk

    @property
    def ijk(self) -> sympy.Basic:
        """
        The handle to the scalar / vector view of this variable.
        """
        return self._ijk

    @property
    def sym(self) -> sympy.Basic:
        """
        The handle to the tensor view of this variable.
        """
        return self._sym

    def _set_vec(self, available):

        if self._lvec == None:
            indexset, subdm = self.mesh.dm.createSubDM(self.field_id)
            # subdm = uw.cython.petsc_discretisation.petsc_fe_create_sub_dm(self.mesh.dm, self.field_id)

            self._lvec = subdm.createLocalVector()
            self._lvec.zeroEntries()  # not sure if required, but to be sure.
            self._gvec = subdm.createGlobalVector()
            self._gvec.setName(self.clean_name)  # This is set for checkpointing.
            self._gvec.zeroEntries()

        self._available = available

    def __del__(self):
        if self._lvec:
            self._lvec.destroy()
        if self._gvec:
            self._gvec.destroy()

    @property
    def vec(self) -> PETSc.Vec:
        """
        The corresponding PETSc local vector for this variable.
        """
        if not self._available:
            raise RuntimeError(
                "Vector must be accessed via the mesh `access()` context manager."
            )
        return self._lvec

    @property
    def data(self) -> numpy.ndarray:
        """
        Numpy proxy array to underlying variable data.
        Note that the returned array is a proxy for all the *local* nodal
        data, and is provided as 1d list.

        For both read and write, this array can only be accessed via the
        mesh `access()` context manager.
        """
        if self._data is None:
            raise RuntimeError(
                "Data must be accessed via the mesh `access()` context manager."
            )
        return self._data

    def min(self) -> Union[float, tuple]:
        """
        The global variable minimum value.
        """
        if not self._lvec:
            raise RuntimeError("It doesn't appear that any data has been set.")

        if self.num_components == 1:
            return self._gvec.min()
        else:
            return tuple(
                [self._gvec.strideMin(i)[1] for i in range(self.num_components)]
            )

    def max(self) -> Union[float, tuple]:
        """
        The global variable maximum value.
        """
        if not self._lvec:
            raise RuntimeError("It doesn't appear that any data has been set.")

        if self.num_components == 1:
            return self._gvec.max()
        else:
            return tuple(
                [self._gvec.strideMax(i)[1] for i in range(self.num_components)]
            )

    def sum(self) -> Union[float, tuple]:
        """
        The global variable sum value.
        """
        if not self._lvec:
            raise RuntimeError("It doesn't appear that any data has been set.")

        if self.num_components == 1:
            return self._gvec.sum()
        else:
            cpts = []
            for i in range(0, self.num_components):
                cpts.append(self._gvec.strideSum(i))

            return tuple(cpts)

    def norm(self, norm_type) -> Union[float, tuple]:
        """
        The global variable norm value.

        norm_type: type of norm, one of
            - 0: NORM 1 ||v|| = sum_i | v_i |. ||A|| = max_j || v_*j ||
            - 1: NORM 2 ||v|| = sqrt(sum_i |v_i|^2) (vectors only)
            - 3: NORM INFINITY ||v|| = max_i |v_i|. ||A|| = max_i || v_i* ||, maximum row sum
        """
        if not self._lvec:
            raise RuntimeError("It doesn't appear that any data has been set.")

        if self.num_components > 1 and norm_type == 2:
            raise RuntimeError("Norm 2 is only available for vectors.")

        if self.num_components == 1:
            return self._gvec.norm(norm_type)
        else:
            return tuple(
                [
                    self._gvec.strideNorm(i, norm_type)
                    for i in range(self.num_components)
                ]
            )

    def mean(self) -> Union[float, tuple]:
        """
        The global variable mean value.
        """
        if not self._lvec:
            raise RuntimeError("It doesn't appear that any data has been set.")

        if self.num_components == 1:
            vecsize = self._gvec.getSize()
            return self._gvec.sum() / vecsize
        else:
            vecsize = self._gvec.getSize() / self.num_components
            return tuple(
                [self._gvec.strideSum(i) / vecsize for i in range(self.num_components)]
            )

    def stats(self):
        """
        The equivalent of mesh.stats but using the native coordinates for this variable
        Not set up for vector variables so we just skip that for now.

        Returns various norms on the mesh using the native mesh discretisation for this
        variable. It is a wrapper on the various _gvec stats routines for the variable.

          - size
          - mean
          - min
          - max
          - sum
          - L2 norm
          - rms
        """

        if self.num_components > 1:
            raise NotImplementedError(
                "stats not available for multi-component variables"
            )

        #       This uses a private work MeshVariable and the various norms defined there but
        #       could either be simplified to just use petsc vectors, or extended to
        #       compute integrals over the elements which is in line with uw1 and uw2

        from petsc4py.PETSc import NormType

        vsize = self._gvec.getSize()
        vmean = self.mean()
        vmax = self.max()[1]
        vmin = self.min()[1]
        vsum = self.sum()
        vnorm2 = self.norm(NormType.NORM_2)
        vrms = vnorm2 / numpy.sqrt(vsize)

        return vsize, vmean, vmin, vmax, vsum, vnorm2, vrms

    @property
    def coords(self) -> numpy.ndarray:
        """
        The array of variable vertex coordinates.
        """
        return self.mesh._get_coords_for_var(self)

    # vector calculus routines - the advantage of using these inbuilt routines is
    # that they are tied to the appropriate mesh definition.

    def divergence(self):
        try:
            return self.mesh.vector.divergence(self.sym)
        except:
            return None

    def gradient(self):
        try:
            return self.mesh.vector.gradient(self.sym)
        except:
            return None

    def curl(self):
        try:
            return self.mesh.vector.curl(self.sym)
        except:
            return None

    def jacobian(self):
        ## validate if this is a vector ?
        return self.mesh.vector.jacobian(self.sym)
