from typing import Optional, Tuple
import contextlib

import numpy as np
import petsc4py.PETSc as PETSc
from mpi4py import MPI

import underworld3 as uw
from underworld3.utilities import _api_tools
import underworld3.timing as timing

comm = MPI.COMM_WORLD

from enum import Enum


class SwarmType(Enum):
    DMSWARM_PIC = 1


class SwarmPICLayout(Enum):
    """
    Particle population fill type:

    SwarmPICLayout.REGULAR     defines points on a regular ijk mesh. Supported by simplex cell types only.
    SwarmPICLayout.GAUSS       defines points using an npoint Gauss-Legendre tensor product quadrature rule.
    SwarmPICLayout.SUBDIVISION defines points on the centroid of a sub-divided reference cell.
    """

    REGULAR = 0
    GAUSS = 1
    SUBDIVISION = 2


class SwarmVariable(_api_tools.Stateful):
    @timing.routine_timer_decorator
    def __init__(
        self,
        name,
        swarm,
        num_components,
        vtype=None,
        dtype=float,
        proxy_degree=1,
        proxy_continuous=True,
        _register=True,
        _proxy=True,
        _nn_proxy=False,
    ):

        if name in swarm.vars.keys():
            raise ValueError("Variable with name {} already exists on swarm.".format(name))

        self.name = name
        self.swarm = swarm
        self.num_components = num_components

        if (dtype == float) or (dtype == "float") or (dtype == np.float64):
            self.dtype = float
            petsc_type = PETSc.ScalarType
        elif (dtype == int) or (dtype == "int") or (dtype == np.int32) or (dtype == np.int64):
            self.dtype = int
            petsc_type = PETSc.IntType
        else:
            raise TypeError(f"Provided dtype={dtype} is not supported. Supported types are 'int' and 'float'.")

        if _register:
            self.swarm.dm.registerField(self.name, self.num_components, dtype=petsc_type)

        self._data = None
        # add to swarms dict
        swarm.vars[name] = self
        self._is_accessed = False

        # create proxy variable
        self._meshVar = None
        if _proxy:
            self.proxy_degree = proxy_degree
            self.proxy_continuous = proxy_continuous
            self._meshVar = uw.discretisation.MeshVariable(
                name, self.swarm.mesh, num_components, vtype, degree=proxy_degree, continuous=proxy_continuous
            )

        self._register = _register
        self._proxy = _proxy
        self._nn_proxy = _nn_proxy

        super().__init__()

    def _update(self):
        """
        This method updates the proxy mesh variable for the current
        swarm & particle variable state.

        Here is how it works:

            1) for each particle, create a distance-weighted average on the node data
            2) check to see which nodes have zero weight / zero contribution and replace with nearest particle value

        Todo: caching the k-d trees etc for the proxy-mesh-variable nodal points
        Todo: some form of global fall-back for when there are no particles on a processor

        """

        # if not proxied, nothing to do. return.
        if not self._meshVar:
            return

        # 1 - Average particles to nodes with distance weighted average

        kd = uw.kdtree.KDTree(self._meshVar.coords)
        kd.build_index()

        with self.swarm.access():
            n, d, b = kd.find_closest_point(self.swarm.data)

            node_values = np.zeros((self._meshVar.coords.shape[0], self.num_components))
            w = np.zeros(self._meshVar.coords.shape[0])

            if not self._nn_proxy:
                for i in range(self.data.shape[0]):
                    if b[i]:
                        node_values[n[i], :] += self.data[i, :] / (1.0e-16 + d[i])
                        w[n[i]] += 1.0 / (1.0e-16 + d[i])

                node_values[np.where(w > 0.0)[0], :] /= w[np.where(w > 0.0)[0]].reshape(-1, 1)

        # 2 - set NN vals on mesh var where w == 0.0

        p_nnmap = self.swarm._get_map(self)

        with self.swarm.mesh.access(self._meshVar), self.swarm.access():
            self._meshVar.data[...] = node_values[...]
            self._meshVar.data[np.where(w == 0.0), :] = self.data[p_nnmap[np.where(w == 0.0)], :]

        return

    @timing.routine_timer_decorator
    def project_from(self, meshvar):
        # use method found in
        # /tmp/petsc-build/petsc/src/dm/impls/swarm/tests/ex2.c
        # to project from fields to particles

        self.swarm.mesh.dm.clearDS()
        self.swarm.mesh.dm.createDS()

        meshdm = meshvar.mesh.dm
        fields = meshvar.field_id
        _, meshvardm = meshdm.createSubDM(fields)

        ksp = PETSc.KSP().create()
        ksp.setOptionsPrefix("swarm_project_from_")
        options = PETSc.Options()
        options.setValue("swarm_project_from_ksp_type", "lsqr")
        options.setValue("swarm_project_from_ksp_rtol", 1e-17)
        options.setValue("swarm_project_from_pc_type", "none")
        ksp.setFromOptions()

        rhs = meshvardm.getGlobalVec()

        M_p = self.swarm.dm.createMassMatrix(meshvardm)

        # make particle weight vector
        f = self.swarm.createGlobalVectorFromField(self.name)

        # create matrix RHS vector, in this case the FEM field fhat with the coefficients vector #alpha
        M = meshvardm.createMassMatrix(meshvardm)
        with meshvar.mesh.access():
            M.multTranspose(meshvar.vec_global, rhs)

        ksp.setOperators(M_p, M_p)
        ksp.solveTranspose(rhs, f)

        self.swarm.dm.destroyGlobalVectorFromField(self.name)
        meshvardm.restoreGlobalVec(rhs)
        meshvardm.destroy()
        ksp.destroy()
        M.destroy()
        M_p.destroy()

    @property
    def data(self):
        if self._data is None:
            raise RuntimeError("Data must be accessed via the swarm `access()` context manager.")
        return self._data

    # @property
    # def fn(self):
    #     return self._meshVar.fn

    @property
    def sym(self):
        return self._meshVar.sym

    @timing.routine_timer_decorator
    def save(self, filename: str, name: Optional[str] = None, index: Optional[int] = None):
        """
        Append variable data to the specified mesh
        checkpoint file. The file must already exist.

        For swarm data, we currently save the proxy
        variable (so this will fail if the variable has
        no proxy value). This allows some form of
        reconstruction of the information on a swarm
        even if it is not an exact mapping.

        This is not ideal for discontinuous fields.

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

        # if not proxied, nothing to do. return.
        if not self._meshVar:
            print("No proxy mesh variable that can be saved")
            return

        self._meshVar.save(filename, name, index)

        return


class IndexSwarmVariable(SwarmVariable):
    """
    The IndexSwarmVariable is a class for managing material point
    behaviour. The material index variable is rendered into a
    collection of masks each representing the extent of one material
    """

    @timing.routine_timer_decorator
    def __init__(
        self,
        name,
        swarm,
        indices=1,
        proxy_degree=1,
        proxy_continuous=True,
    ):

        self.indices = indices

        # These are the things we require of the generic swarm variable type
        super().__init__(
            name,
            swarm,
            num_components=1,
            vtype=None,
            dtype=int,
            _proxy=False,
        )

        # The indices variable defines how many level set maps we create as components in the proxy variable

        import sympy

        self._MaskArray = sympy.Matrix.zeros(1, self.indices)
        self._meshLevelSetVars = [None] * self.indices

        for i in range(indices):
            self._meshLevelSetVars[i] = uw.discretisation.MeshVariable(
                name + R"^{[" + str(i) + R"]}",
                self.swarm.mesh,
                num_components=1,
                degree=proxy_degree,
                continuous=proxy_continuous,
            )
            self._MaskArray[0, i] = self._meshLevelSetVars[i].sym[0, 0]

        return

    # This is the sympy vector interface - it's meaningless if these are not spatial arrays
    @property
    def sym(self):
        return self._MaskArray

    def _update(self):
        """
        This method updates the proxy mesh (vector) variable for the index variable on the current swarm locations

        Here is how it works:

            1) for each particle, create a distance-weighted average on the node data
            2) for each index in the set, we create a mask mesh variable by mapping 1.0 wherever the
               index matches and 0.0 where it does not.

        NOTE: If no material is identified with a given nodal value, the default is to material zero

        """

        kd = uw.kdtree.KDTree(self._meshLevelSetVars[0].coords)
        kd.build_index()

        for ii in range(self.indices):
            meshVar = self._meshLevelSetVars[ii]

            # 1 - Average particles to nodes with distance weighted average
            with self.swarm.mesh.access(meshVar), self.swarm.access():
                n, d, b = kd.find_closest_point(self.swarm.data)

                node_values = np.zeros((meshVar.data.shape[0],))
                w = np.zeros((meshVar.data.shape[0],))

                for i in range(self.data.shape[0]):
                    if b[i]:
                        node_values[n[i]] += np.isclose(self.data[i], ii) / (1.0e-16 + d[i])
                        w[n[i]] += 1.0 / (1.0e-16 + d[i])

                node_values[np.where(w > 0.0)[0]] /= w[np.where(w > 0.0)[0]]

            # 2 - set NN vals on mesh var where w == 0.0

            with self.swarm.mesh.access(meshVar), self.swarm.access():
                meshVar.data[...] = node_values[...].reshape(-1, 1)

                # Need to document this assumption, if there is no material found,
                # assume the default material (0). An alternative would be to impose
                # a near-neighbour hunt for a valid material and set that one.

                if ii == 0:
                    meshVar.data[np.where(w == 0.0)] = 1.0
                else:
                    meshVar.data[np.where(w == 0.0)] = 0.0

        return


# @typechecked
class Swarm(_api_tools.Stateful):

    instances = 0

    @timing.routine_timer_decorator
    def __init__(self, mesh):

        Swarm.instances += 1

        self.mesh = mesh
        self.dim = mesh.dim
        self.cdim = mesh.cdim
        self.dm = PETSc.DMSwarm().create()
        self.dm.setDimension(self.dim)
        self.dm.setType(SwarmType.DMSWARM_PIC.value)
        self.dm.setCellDM(mesh.dm)
        self._data = None

        # dictionary for variables
        import weakref

        self._vars = weakref.WeakValueDictionary()

        # add variable to handle particle coords
        self._coord_var = SwarmVariable("DMSwarmPIC_coor", self, self.cdim, dtype=float, _register=False, _proxy=False)

        # add variable to handle particle cell id
        self._cellid_var = SwarmVariable("DMSwarm_cellid", self, 1, dtype=int, _register=False, _proxy=False)

        # add variable to hold swarm coordinates during position updates
        self._X0 = uw.swarm.SwarmVariable("DMSwarm_X0", self, self.cdim, dtype=float, _register=True, _proxy=False)
        self._X0_uninitialised = True

        self._index = None
        self._nnmapdict = {}

        super().__init__()

    @property
    def data(self):
        return self.particle_coordinates.data

    @property
    def particle_coordinates(self):
        return self._coord_var

    @property
    def particle_cellid(self):
        return self._cellid_var

    @timing.routine_timer_decorator
    def populate(
        self,
        fill_param: Optional[int] = 3,
        layout: Optional[SwarmPICLayout] = None,
    ):
        (
            """
        Populate the swarm with particles throughout the domain.

        """
            + SwarmPICLayout.__doc__
            + """

        When using SwarmPICLayout.REGULAR,     `fill_param` defines the number of points in each spatial direction.
        When using SwarmPICLayout.GAUSS,       `fill_param` defines the number of quadrature points in each spatial direction.
        When using SwarmPICLayout.SUBDIVISION, `fill_param` defines the number times the reference cell is sub-divided.

        Parameters
        ----------
        fill_param:
            Parameter determining the particle count per cell for the given layout.
        layout:
            Type of layout to use. Defaults to `SwarmPICLayout.REGULAR` for mesh objects with simplex
            type cells, and `SwarmPICLayout.GAUSS` otherwise.



        """
        )

        self.fill_param = fill_param

        """
        Currently (2021.11.15) supported by PETSc release 3.16.x
 
        When using a DMPLEX the following case are supported:
              (i) DMSWARMPIC_LAYOUT_REGULAR: 2D (triangle),
             (ii) DMSWARMPIC_LAYOUT_GAUSS: 2D and 3D provided the cell is a tri/tet or a quad/hex,
            (iii) DMSWARMPIC_LAYOUT_SUBDIVISION: 2D and 3D for quad/hex and 2D tri.

        So this means, simplex mesh in 3D only supports GAUSS 

        """

        if layout == None:
            if self.mesh.isSimplex == True and self.dim == 2 and fill_param > 1:
                layout = SwarmPICLayout.REGULAR
            else:
                layout = SwarmPICLayout.GAUSS

        if not isinstance(layout, SwarmPICLayout):
            raise ValueError("'layout' must be an instance of 'SwarmPICLayout'")

        self.layout = layout
        self.dm.finalizeFieldRegister()

        ## Commenting this out for now.
        ## Code seems to operate fine without it, and the
        ## existing values are wrong. It should be something like
        ## `(elend-elstart)*fill_param^dim` for quads, and around
        ## half that for simplices, depending on layout.
        # elstart,elend = self.mesh.dm.getHeightStratum(0)
        # self.dm.setLocalSizes((elend-elstart) * fill_param, 0)

        self.dm.insertPointUsingCellDM(self.layout.value, fill_param)
        return  # self # LM: Is there any reason to return self ?

    @timing.routine_timer_decorator
    def add_variable(self, name, num_components=1, dtype=float, proxy_degree=2, _nn_proxy=False):
        return SwarmVariable(name, self, num_components, dtype=dtype, proxy_degree=proxy_degree, _nn_proxy=_nn_proxy)

    @property
    def vars(self):
        return self._vars

    def access(self, *writeable_vars: SwarmVariable):
        """
        This context manager makes the underlying swarm variables data available to
        the user. The data should be accessed via the variables `data` handle.

        As default, all data is read-only. To enable writeable data, the user should
        specify which variable they wish to modify.

        At the conclusion of the users context managed block, numerous further operations
        will be automatically executed. This includes swarm parallel migration routines
        where the swarm's `particle_coordinates` variable has been modified. The swarm
        variable proxy mesh variables will also be updated for modifed swarm variables.

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

        uw.timing._incrementDepth()
        stime = time.time()

        deaccess_list = []
        for var in self.vars.values():
            # if already accessed within higher level context manager, continue.
            if var._is_accessed == True:
                continue
            # set flag so variable status can be known elsewhere
            var._is_accessed = True
            # add to de-access list to rewind this later
            deaccess_list.append(var)
            # grab numpy object, setting read only if necessary
            var._data = self.dm.getField(var.name).reshape((-1, var.num_components))
            if var not in writeable_vars:
                var._old_data_flag = var._data.flags.writeable
                var._data.flags.writeable = False
            else:
                # increment variable state
                var._increment()
        # if particles moving, update swarm state
        if self.particle_coordinates in writeable_vars:
            self._increment()

        # Create a class which specifies the required context
        # manager hooks (`__enter__`, `__exit__`).
        class exit_manager:
            def __init__(self, swarm):
                self.em_swarm = swarm

            def __enter__(self):
                pass

            def __exit__(self, *args):
                for var in self.em_swarm.vars.values():
                    # only de-access variables we have set access for.
                    if var not in deaccess_list:
                        continue
                    # set this back, although possibly not required.
                    if var not in writeable_vars:
                        var._data.flags.writeable = var._old_data_flag
                    var._data = None
                    self.em_swarm.dm.restoreField(var.name)
                    var._is_accessed = False
                # do particle migration if coords changes
                if self.em_swarm.particle_coordinates in writeable_vars:
                    # let's use the mesh index to update the particles owning cells.
                    # note that the `petsc4py` interface is more convenient here as the
                    # `SwarmVariable.data` interface is controlled by the context manager
                    # that we are currently within, and it is therefore too easy to
                    # get things wrong that way.
                    cellid = self.em_swarm.dm.getField("DMSwarm_cellid")
                    coords = self.em_swarm.dm.getField("DMSwarmPIC_coor").reshape((-1, self.em_swarm.dim))
                    cellid[:] = self.em_swarm.mesh.get_closest_cells(coords)
                    self.em_swarm.dm.restoreField("DMSwarmPIC_coor")
                    self.em_swarm.dm.restoreField("DMSwarm_cellid")
                    # now migrate.
                    self.em_swarm.dm.migrate(remove_sent_points=True)
                    # void these things too
                    self.em_swarm._index = None
                    self.em_swarm._nnmapdict = {}
                # do var updates
                for var in self.em_swarm.vars.values():
                    # if swarm migrated, update all.
                    # if var updated, update var.
                    if (self.em_swarm.particle_coordinates in writeable_vars) or (var in writeable_vars):
                        var._update()

                uw.timing._decrementDepth()
                uw.timing.log_result(time.time() - stime, "Swarm.access", 1)

        return exit_manager(self)

    def _get_map(self, var):
        # generate tree if not avaiable
        if not self._index:
            with self.access():
                self._index = uw.kdtree.KDTree(self.data)

        # get or generate map
        meshvar_coords = var._meshVar.coords
        # we can't use numpy arrays directly as keys in python dicts, so
        # we'll use `xxhash` to generate a hash of array.
        # this shouldn't be an issue performance wise but we should test to be
        # sufficiently confident of this.
        import xxhash

        h = xxhash.xxh64()
        h.update(meshvar_coords)
        digest = h.intdigest()
        if digest not in self._nnmapdict:
            self._nnmapdict[digest] = self._index.find_closest_point(meshvar_coords)[0]
        return self._nnmapdict[digest]

    def advection(self, V_fn, delta_t, order=2, corrector=False, restore_points_to_domain_func=None):

        X0 = self._X0

        # Use current velocity to estimate where the particles would have
        # landed in an implicit step.

        # ? how does this interact with the particle restoration function ?

        V_fn_matrix = self.mesh.vector.to_matrix(V_fn)

        if corrector == True and not self._X0_uninitialised:
            with self.access(self.particle_coordinates):
                v_at_Vpts = np.zeros_like(self.data)

                for d in range(self.dim):
                    v_at_Vpts[:, d] = uw.function.evaluate(V_fn_matrix[d], self.data).reshape(-1)

                corrected_position = X0.data + delta_t * v_at_Vpts
                if restore_points_to_domain_func is not None:
                    corrected_position = restore_points_to_domain_func(corrected_position)

                updated_current_coords = 0.5 * (corrected_position + self.data)

                # validate_coords to ensure they live within the domain (or there will be trouble)

                if restore_points_to_domain_func is not None:
                    updated_current_coords = restore_points_to_domain_func(updated_current_coords)

                self.data[...] = updated_current_coords

        with self.access(X0):
            X0.data[...] = self.data[...]
            self._X0_uninitialised = False

        # Mid point algorithm (2nd order)
        if order == 2:
            with self.access(self.particle_coordinates):

                v_at_Vpts = np.zeros_like(self.data)

                for d in range(self.dim):
                    v_at_Vpts[:, d] = uw.function.evaluate(V_fn_matrix[d], self.data).reshape(-1)

                mid_pt_coords = self.data[...] + 0.5 * delta_t * v_at_Vpts

                # validate_coords to ensure they live within the domain (or there will be trouble)

                if restore_points_to_domain_func is not None:
                    mid_pt_coords = restore_points_to_domain_func(mid_pt_coords)

                self.data[...] = mid_pt_coords

                # if (uw.mpi.rank == 0):
                #     print("Updated mid point position", flush=True)

                ## Let the swarm be updated, and then move the rest of the way

            with self.access(self.particle_coordinates):

                v_at_Vpts = np.zeros_like(self.data)

                for d in range(self.dim):
                    v_at_Vpts[:, d] = uw.function.evaluate(V_fn_matrix[d], self.data).reshape(-1)

                # if (uw.mpi.rank == 0):
                #     print("Re-launch from X0", flush=True)

                new_coords = X0.data[...] + delta_t * v_at_Vpts

                # validate_coords to ensure they live within the domain (or there will be trouble)
                if restore_points_to_domain_func is not None:
                    new_coords = restore_points_to_domain_func(new_coords)

                # if (uw.mpi.rank == 0):
                #     print("Update", flush=True)

                self.data[...] = new_coords

        # Previous position algorithm (cf above) - we use the previous step as the
        # launch point using the current velocity field. This gives a correction to the previous
        # landing point.

        # assumes X0 is stored from the previous step ... midpoint is needed in the first step

        # forward Euler (1st order)
        else:
            with self.access(self.particle_coordinates):
                for d in range(self.dim):
                    v_at_Vpts[:, d] = uw.function.evaluate(V_fn[d], self.data).reshape(-1)

                new_coords = self.data + delta_t * v_at_Vpts

                # validate_coords to ensure they live within the domain (or there will be trouble)

                if restore_points_to_domain_func is not None:
                    new_coords = restore_points_to_domain_func(new_coords)

                self.data[...] = new_coords

        return
