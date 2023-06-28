from xmlrpc.client import Boolean

import sympy
from sympy import sympify

from typing import Optional
from petsc4py import PETSc

import underworld3 
import underworld3 as uw
from   underworld3.utilities._jitextension import getext  
import underworld3.timing as timing
from underworld3.utilities._api_tools import uw_object
from underworld3.utilities._api_tools import class_or_instance_method

include "petsc_extras.pxi"

## Note, a generic base class could be introduced here 
## which would have the validation, essential properties,
## boundary condition routines, 
## methods for the petsc_options ... 


class Solver(uw_object):
    r"""
    The Generic `Solver` is used to build the `SNES Solvers`
        - `SNES_Scalar`
        - `SNES_Vector`
        - `SNES_Stokes`

    This class is not intended to be used directly
    """    

    def _object_viewer(self):
        '''This will add specific information about this object to the generic class viewer
        '''
        from IPython.display import Latex, Markdown, display
        from textwrap import dedent


        display(Markdown(fr"This solver is formulated in {self.mesh.dim} dimensions"))
        return


    @timing.routine_timer_decorator
    def _setup_problem_description(self):

        ## Derived classes may over-ride these methods to better match the equation template. 

        # We might choose to modify the definition of F0  / F1 
        # by changing this function in a sub-class
        # For example, to simplify the user interface by pre-definining
        # the form of the input. See the projector class for an example 

        # f0 residual term (weighted integration) - scalar RHS function
        self._f0 = self.F0 # some_expression_F0(self._u.sym, self._L)

        # f1 residual term (integration by parts / gradients)
        self._f1 = self.F1 # some_expresion_F1(self._u.sym, self._L)

        return 

    @timing.routine_timer_decorator
    def add_neumann_bc(self, fn, boundaries, components=[0]):
        # switch to numpy arrays
        # ndmin arg forces an array to be generated even
        # where comps/indices is a single value.
        self.is_setup = False
        import numpy as np
        components = np.array(components, dtype=np.int32, ndmin=1)
        boundaries = np.array(boundaries, dtype=object,   ndmin=1)
        from collections import namedtuple
        BC = namedtuple('BC', ['components', 'fn', 'boundaries', 'type'])
        self.bcs.append(BC(components,sympify(fn),boundaries, "neumann"))

    @timing.routine_timer_decorator
    def add_dirichlet_bc(self, fn, boundaries, components=[0]):
        # switch to numpy arrays
        # ndmin arg forces an array to be generated even
        # where comps/indices is a single value.
        self.is_setup = False
        import numpy as np
        components = np.array(components, dtype=np.int32, ndmin=1)
        boundaries = np.array(boundaries, dtype=object,   ndmin=1)
        from collections import namedtuple
        BC = namedtuple('BC', ['components', 'fn', 'boundaries', 'type'])
        self.bcs.append(BC(components,sympify(fn),boundaries, 'dirichlet'))    

    ## Properties that are common to all solvers
    
    @property
    def F0(self):
        return self._F0
    @F0.setter
    def F0(self, value):
        self.is_setup = False
        self._F0 = sympify(value)

    @property
    def F1(self):
        return self._F1
    @F1.setter
    def F1(self, value):
        self.is_setup = False
        self._F1 = sympify(value)

    @property
    def u(self):
        return self._u

    @property
    def constitutive_model(self):
        return self._constitutive_model

    @constitutive_model.setter
    def constitutive_model(self, model):

        # is the model appropriate for SNES_Scalar solvers ?
        self.is_setup = False
        self._constitutive_model = model


    def validate_solver(self):
        """
        Checks to see if the required properties have been set.
        Over-ride this one if you want to check specifics for your solver"""

        name = self.__class__.__name__

        if not isinstance(self.u, uw.discretisation.MeshVariable):
            print(f"Vector of unknowns required")
            print(f"{name}.u = uw.discretisation.MeshVariable(...)")

        if not isinstance(self.constitutive_model, uw.systems.constitutive_models.Constitutive_Model):
            print(f"Constitutive model required")
            print(f"{name}.constitutive_model = uw.constitutive_models...")          

        return


## Specific to dimensionality


class SNES_Scalar(Solver):
    r"""
    The `SNES_Scalar` solver class provides functionality for solving the scalar conservation problem in the unknown $u$:

    $$
    \nabla \cdot \color{Blue}{\mathrm{F}(u, \nabla u, \dot{u}, \nabla\dot{u})} - 
    \color{Green}{\mathrm{f} (u, \nabla u, \dot{u}, \nabla\dot{u})} = 0
    $$

    where $\mathrm{f}$ is a source term for $u$, $\mathrm{F}$ is a flux term that relates the rate of change of $u$ to the gradients $\nabla u$, and $\dot{\mathrm{u}}$ is the Lagrangian time-derivative of $u$. 

    $u$ is a scalar `underworld` `meshVariable` and $\mathrm{f}$, $\mathrm{F}$ are arbitrary sympy expressions of the coodinate variables of the mesh. 

    This class is used as a base layer for building solvers which translate from the common
    physical conservation laws into this general mathematical form. 
    """
    instances = 0

    @timing.routine_timer_decorator
    def __init__(self, 
                 mesh     : uw.discretisation.Mesh, 
                 u_Field  : uw.discretisation.MeshVariable = None, 
                 solver_name: str = "",
                 verbose    = False):

        ## Keep track

        SNES_Scalar.instances += 1

        self.name = solver_name

        ## Todo: this is obviously not particularly robust

        if solver_name != "" and not solver_name.endswith("_"):
            self.petsc_options_prefix = solver_name+"_"
        else:
            self.petsc_options_prefix = solver_name

        options = PETSc.Options()
        # options["dm_adaptor"]= "pragmatic"
    
        self.petsc_options = PETSc.Options(self.petsc_options_prefix)

        # Here we can set some defaults for this set of KSP / SNES solvers
        self.petsc_options["snes_type"] = "newtonls"
        self.petsc_options["ksp_type"] = "gmres"
        self.petsc_options["pc_type"] = "gamg"
        self.petsc_options["pc_gamg_type"] = "agg"
        self.petsc_options["pc_gamg_repartition"]  = True  
        self.petsc_options["pc_mg_type"]  = "additive"
        self.petsc_options["pc_gamg_agg_nsmooths"] = 2
        self.petsc_options["mg_levels_ksp_max_it"] = 3
        self.petsc_options["mg_levels_ksp_converged_maxits"] = None        
        self.petsc_options["snes_converged_reason"] = None
        self.petsc_options["snes_monitor_short"] = None
        self.petsc_options["snes_rtol"] = 1.0e-4
        self.petsc_options["mg_levels_ksp_max_it"] = 3

        self._u = u_Field
        self.mesh = mesh
        self._F0 = sympy.Matrix.zeros(1,1)
        self._F1 = sympy.Matrix.zeros(1,mesh.dim)
        self._L = self._u.sym.jacobian(self.mesh.CoordinateSystem.N)

        self.bcs = []
        self._constitutive_model = None

        self.is_setup = False
        self.verbose = verbose

        # Build the DM / FE structures (should be done on remeshing)

        self._build_dm_and_mesh_discretisation()
        self._rebuild_after_mesh_update = self._build_dm_and_mesh_discretisation

        # Some other setup 

        self.mesh._equation_systems_register.append(self)

        super().__init__()


    @timing.routine_timer_decorator
    def _build_dm_and_mesh_discretisation(self):

        degree = self._u.degree
        mesh = self.mesh

        if self.verbose:
            print(f"{uw.mpi.rank}: Building dm for {self.name}")

        if mesh.qdegree < degree: 
            print(f"Caution - the mesh quadrature ({mesh.qdegree})is lower")
            print(f"than {degree} which is required by the {self.name} solver")

        self.dm = mesh.dm.clone()

        if self.verbose:
            print(f"{uw.mpi.rank}: Building FE / quadrature for {self.name}")

        # create private variables using standard quadrature order from the mesh
        
        options = PETSc.Options()
        options.setValue("{}_private_petscspace_degree".format(self.petsc_options_prefix), degree) # for private variables
        options.setValue("{}_private_petscdualspace_lagrange_continuity".format(self.petsc_options_prefix), self.u.continuous)
        options.setValue("{}_private_petscdualspace_lagrange_node_endpoints".format(self.petsc_options_prefix), False)

        self.petsc_fe_u = PETSc.FE().createDefault(mesh.dim, 1, mesh.isSimplex, mesh.qdegree, "{}_private_".format(self.petsc_options_prefix), PETSc.COMM_WORLD,)
        self.petsc_fe_u_id = self.dm.getNumFields()
        self.dm.setField( self.petsc_fe_u_id, self.petsc_fe_u )
        self.is_setup = False

        if self.verbose:
            print(f"{uw.mpi.rank}: Building DS for {self.name}")

        self.dm.createDS()

        return

    @timing.routine_timer_decorator
    def _setup_terms(self, verbose=False):
        import sympy

        N = self.mesh.N
        dim = self.mesh.dim
        cdim = self.mesh.cdim

        sympy.core.cache.clear_cache()

        ## The residual terms describe the problem and 
        ## can be changed by the user in inherited classes

        self._build_dm_and_mesh_discretisation()
        self._setup_problem_description()

        ## The jacobians are determined from the above (assuming we 
        ## do not concern ourselves with the zeros)

        F0 = sympy.Array(self._f0).reshape(1).as_immutable()
        F1 = sympy.Array(self._f1).reshape(dim).as_immutable()

        U = sympy.Array(self._u.sym).reshape(1).as_immutable() # scalar works better in derive_by_array
        L = sympy.Array(self._L).reshape(cdim).as_immutable() # unpack one index here too

        fns_residual = [F0, F1] 

        G0 = sympy.derive_by_array(F0, U)
        G1 = sympy.derive_by_array(F0, L)
        G2 = sympy.derive_by_array(F1, U)
        G3 = sympy.derive_by_array(F1, L)

        # Re-organise if needed / make hashable
        
        self._G0 = sympy.ImmutableMatrix(G0)
        self._G1 = sympy.ImmutableMatrix(G1)
        self._G2 = sympy.ImmutableMatrix(G2)
        self._G3 = sympy.ImmutableMatrix(G3)

        ##################

        fns_jacobian = (self._G0, self._G1, self._G2, self._G3)

        ################## 

        # generate JIT code.
        # first, we must specify the primary fields.
        # these are fields for which the corresponding sympy functions 
        # should be replaced with the primary (instead of auxiliary) petsc 
        # field value arrays. in this instance, we want to switch out 
        # `self.u` and `self.p` for their primary field 
        # petsc equivalents. without specifying this list, 
        # the aux field equivalents will be used instead, which 
        # will give incorrect results for non-linear problems.
        # note also that the order here is important.

        prim_field_list = [self.u,]
        cdef PtrContainer ext = getext(self.mesh, tuple(fns_residual), tuple(fns_jacobian), [x[1] for x in self.bcs], primary_field_list=prim_field_list, verbose=verbose)

        # set functions 
        self.dm.createDS()
        cdef DS ds = self.dm.getDS()
        PetscDSSetResidual(ds.ds, 0, ext.fns_residual[0], ext.fns_residual[1])
        # TODO: check if there's a significant performance overhead in passing in 
        # identically `zero` pointwise functions instead of setting to `NULL`
        PetscDSSetJacobian(ds.ds, 0, 0, ext.fns_jacobian[0], ext.fns_jacobian[1], ext.fns_jacobian[2], ext.fns_jacobian[3])
        
        cdef int ind=1
        cdef int [::1] comps_view  # for numpy memory view
        cdef DM cdm = self.dm

        for index,bc in enumerate(self.bcs):
            comps_view = bc.components
            if uw.mpi.rank == 0 and self.verbose:
                print("Setting bc {} ({})".format(index, bc.type))
                print(" - components: {}".format(bc.components))
                print(" - boundary:   {}".format(bc.boundaries))
                print(" - fn:         {} ".format(bc.fn))

            for boundary in bc.boundaries:
                label = self.dm.getLabel(boundary)
                if not label:
                    if self.verbose == True:
                        print(f"Discarding bc {boundary} which has no corresponding mesh / dm label")
                    continue

                iset = label.getNonEmptyStratumValuesIS()
                if iset:
                    label_values = iset.getIndices()
                    if len(label_values > 0):
                        value = label_values[0]  # this is only one value in the label ... 
                        ind = value
                    else:
                        ind = -1

                # use type 5 bc for `DM_BC_ESSENTIAL_FIELD` enum
                # use type 6 bc for `DM_BC_NATURAL_FIELD` enum  (is this implemented for non-zero values ?)
                if bc.type == 'neumann':
                    bc_type = 6
                else:
                    bc_type = 5

                PetscDSAddBoundary_UW( cdm.dm, bc_type, str(boundary).encode('utf8'), str(boundary).encode('utf8'), 0, comps_view.shape[0], <const PetscInt *> &comps_view[0], <void (*)()>ext.fns_bcs[index], NULL, 1, <const PetscInt *> &ind, NULL)

        self.dm.setUp()

        self.dm.createClosureIndex(None)
        self.snes = PETSc.SNES().create(PETSc.COMM_WORLD)
        self.snes.setDM(self.dm)
        self.snes.setOptionsPrefix(self.petsc_options_prefix)
        self.snes.setFromOptions()
        cdef DM dm = self.dm
        DMPlexSetSNESLocalFEM(dm.dm, NULL, NULL, NULL)

        self.is_setup = True
        self.constitutive_model._solver_is_setup = True

    @timing.routine_timer_decorator
    def solve(self, 
              zero_init_guess: bool =True, 
              _force_setup:    bool =False ):
        """
        Generates solution to constructed system.

        Params
        ------
        zero_init_guess:
            If `True`, a zero initial guess will be used for the 
            system solution. Otherwise, the current values of `self.u` 
            and `self.p` will be used.
        """
        if (not self.is_setup) or (not self.constitutive_model._solver_is_setup) or _force_setup:
            self._setup_terms()

        gvec = self.dm.getGlobalVec()

        if not zero_init_guess:
            with self.mesh.access():
                self.dm.localToGlobal(self.u.vec, gvec)
        else:
            gvec.array[:] = 0.

        # Set quadrature to consistent value given by mesh quadrature.
        # self.mesh._align_quadratures()

        # Call `createDS()` on aux dm. This is necessary after the 
        # quadratures are set above, as it generates the tablatures 
        # from the quadratures (among other things no doubt). 
        # TODO: What does createDS do?
        # TODO: What are the implications of calling this every solve.

        self.mesh.dm.clearDS()
        self.mesh.dm.createDS()

        cdef DM dm = self.dm

        self.mesh.update_lvec()
        cdef Vec cmesh_lvec
        # PETSc == 3.16 introduced an explicit interface 
        # for setting the aux-vector which we'll use when available.
        cmesh_lvec = self.mesh.lvec
        ierr = DMSetAuxiliaryVec_UW(dm.dm, NULL, 0, 0, cmesh_lvec.vec); CHKERRQ(ierr)

        # solve
        self.snes.solve(None, gvec)

        lvec = self.dm.getLocalVec()
        cdef Vec clvec = lvec
        # Copy solution back into user facing variable
        with self.mesh.access(self.u,):
            self.dm.globalToLocal(gvec, lvec)
            # add back boundaries.
            ierr = DMPlexSNESComputeBoundaryFEM(dm.dm, <void*>clvec.vec, NULL); CHKERRQ(ierr)
            self.u.vec.array[:] = lvec.array[:]

        self.dm.restoreLocalVec(lvec)
        self.dm.restoreGlobalVec(gvec)



### =================================

# LM: this is probably not something we need ... The petsc interface is 
# general enough to have one class to handle Vector and Scalar

class SNES_Vector(Solver):
    r"""
    The `SNES_Vector` solver class provides functionality for solving the vector conservation problem in the unknown $\mathbf{u}$:

    $$
    \nabla \cdot \color{Blue}{\mathbf{F}(\mathbf{u}, \nabla \mathbf{u}, \dot{\mathbf{u}}, \nabla\dot{\mathbf{u}u})} - 
    \color{Green}{\mathbf{f} (\mathbf{u}, \nabla \mathbf{u}, \dot{\mathbf{u}}, \nabla\dot{\mathbf{u}u})} = 0
    $$

    where $\mathbf{f}$ is a source term for $u$, $\mathbf{F}$ is a flux term that relates
    the rate of change of $\mathbf{u}$ to the gradients $\nabla \mathbf{u}$, and $\dot{\mathbf{u}}$
    is the Lagrangian time-derivative of $\mathbf{u}$. 

    $\mathbf{u}$ is a vector `underworld` `meshVariable` and $\mathbf{f}$, $\mathbf{F}$ 
    are arbitrary sympy expressions of the coodinate variables of the mesh and may include other
    `meshVariable` and `swarmVariable` objects. 

    This class is used as a base layer for building solvers which translate from the common
    physical conservation laws into this general mathematical form. 
    """
    instances = 0

    @timing.routine_timer_decorator
    def __init__(self, 
                 mesh     : uw.discretisation.Mesh, 
                 u_Field  : uw.discretisation.MeshVariable = None, 
                 degree     = 2,
                 solver_name: str = "",
                 verbose    = False):

        ## Keep track

        SNES_Vector.instances += 1
        self.name = solver_name


        ## Todo: this is obviously not particularly robust

        if solver_name != "" and not solver_name.endswith("_"):
            self.petsc_options_prefix = solver_name+"_"
        else:
            self.petsc_options_prefix = solver_name

        options = PETSc.Options()
        # options["dm_adaptor"]= "pragmatic"

        self.petsc_options = PETSc.Options(self.petsc_options_prefix)

        # Here we can set some defaults for this set of KSP / SNES solvers
        self.petsc_options["snes_type"] = "newtonls"
        self.petsc_options["ksp_rtol"] = 1.0e-3
        self.petsc_options["ksp_monitor"] = None
        self.petsc_options["ksp_type"] = "gmres"
        self.petsc_options["pc_type"] = "gamg"
        self.petsc_options["pc_gamg_type"] = "agg"
        self.petsc_options["pc_gamg_repartition"]  = True  
        self.petsc_options["pc_mg_type"]  = "additive"
        self.petsc_options["pc_gamg_agg_nsmooths"] = 2
        self.petsc_options["snes_converged_reason"] = None
        self.petsc_options["snes_monitor_short"] = None
        self.petsc_options["snes_rtol"] = 1.0e-3
        self.petsc_options["mg_levels_ksp_max_it"] = 3
        self.petsc_options["mg_levels_ksp_converged_maxits"] = None


        ## Todo: some validity checking on the size / type of u_Field supplied
        ##if not u_Field:
        ##     self._u = uw.discretisation.MeshVariable( mesh=mesh, num_components=mesh.dim, name="Uv{}".format(SNES_Scalar.instances),
        ##                                     vtype=uw.VarType.SCALAR, degree=degree )
        ## else:
            
        self._u = u_Field
        self.mesh = mesh
        self._F0 = sympy.Matrix.zeros(1, self.mesh.dim)
        self._F1 = sympy.Matrix.zeros(self.mesh.dim, self.mesh.dim)

        ## sympy.Matrix

        self._U = self._u.sym

        ## sympy.Matrix - gradient tensor   
        self._L = self._u.sym.jacobian(self.mesh.CoordinateSystem.N) # This works for vector / vector inputs

        self.bcs = []
        self._constitutive_model = None

        self.is_setup = False
        self.verbose = verbose

        # Build the DM / FE structures (should be done on remeshing)

        self._build_dm_and_mesh_discretisation()
        self._rebuild_after_mesh_update = self._build_dm_and_mesh_discretisation

        # Some other setup 

        self.mesh._equation_systems_register.append(self)

        super().__init__()



    @timing.routine_timer_decorator
    def _build_dm_and_mesh_discretisation(self):

        degree = self._u.degree
        mesh = self.mesh
        u_cpts = self._u.shape[1]

        if mesh.qdegree < degree: 
            print(f"Caution - the mesh quadrature ({mesh.qdegree})is lower")
            print(f"than {degree} which is required by the {self.name} solver")

        self.dm = mesh.dm.clone()

        # create private variables
        options = PETSc.Options()
        options.setValue("{}_private_petscspace_degree".format(self.petsc_options_prefix), degree) # for private variables
        options.setValue("{}_private_petscdualspace_lagrange_continuity".format(self.petsc_options_prefix), self.u.continuous)
        options.setValue("{}_private_petscdualspace_lagrange_node_endpoints".format(self.petsc_options_prefix), False)
        
        self.petsc_fe_u = PETSc.FE().createDefault(mesh.dim, u_cpts, mesh.isSimplex, mesh.qdegree,"{}_private_".format(self.petsc_options_prefix), PETSc.COMM_WORLD)
        self.petsc_fe_u_id = self.dm.getNumFields()
        self.dm.setField( self.petsc_fe_u_id, self.petsc_fe_u )

        self.is_setup = False
        self.dm.createDS()

        return

    def validate_solver(self):
        """Checks to see if the required properties have been set"""

        name = self.__class__.__name__

        if not isinstance(self.u, uw.discretisation.MeshVariable):
            print(f"Vector of unknowns required")
            print(f"{name}.u = uw.discretisation.MeshVariable(...)")
            raise RuntimeError("Unknowns: MeshVariable is required")       

        if not isinstance(self.constitutive_model, uw.systems.constitutive_models.Constitutive_Model):
            print(f"Constitutive model required")
            print(f"{name}.constitutive_model = uw.constitutive_models...")   
            raise RuntimeError("Constitutive Model is required")       

        return


    # The properties that are used in the problem description
    # F0 is a vector function (can include u, grad_u)
    # F1_i is a vector valued function (can include u, grad_u)

    # We don't add any validation here ... we should check that these
    # can be ingested by the _setup_terms() function

    @timing.routine_timer_decorator
    def _setup_terms(self, verbose=False):
        import sympy

        N = self.mesh.N
        dim = self.mesh.dim

        ## The residual terms describe the problem and 
        ## can be changed by the user in inherited classes

        sympy.core.cache.clear_cache()

        self._build_dm_and_mesh_discretisation()
        self._setup_problem_description()

        ## The jacobians are determined from the above (assuming we 
        ## do not concern ourselves with the zeros)
        ## Convert to arrays for the moment to allow 1D arrays (size dim, not 1xdim)
        ## otherwise we have many size-1 indices that we have to collapse

        F0 = sympy.Array(self.mesh.vector.to_matrix(self._f0)).reshape(dim)
        F1 = sympy.Array(self._f1).reshape(dim,dim)

        # JIT compilation needs immutable, matrix input (not arrays)
        u_F0 = sympy.ImmutableDenseMatrix(F0)
        u_F1 = sympy.ImmutableDenseMatrix(F1)
        fns_residual = [u_F0, u_F1] 

        # This is needed to eliminate extra dims in the tensor
        U = sympy.Array(self._u.sym).reshape(dim)

        G0 = sympy.derive_by_array(F0, U)
        G1 = sympy.derive_by_array(F0, self._L)
        G2 = sympy.derive_by_array(F1, U)
        G3 = sympy.derive_by_array(F1, self._L)

        # reorganise indices from sympy to petsc ordering 
        # reshape to Matrix form
        # Make hashable (immutable)

        permutation = (0,3,1,2)

        self._G0 = sympy.ImmutableMatrix(G0.reshape(dim,dim))
        self._G1 = sympy.ImmutableMatrix(sympy.permutedims(G1, (2,1,0)  ).reshape(dim,dim*dim))
        self._G2 = sympy.ImmutableMatrix(sympy.permutedims(G2, (2,1,0)  ).reshape(dim*dim,dim))
        self._G3 = sympy.ImmutableMatrix(sympy.permutedims(G3, permutation).reshape(dim*dim,dim*dim))

        ##################

        fns_jacobian = (self._G0, self._G1, self._G2, self._G3)

        ################## 

        # generate JIT code.
        # first, we must specify the primary fields.
        # these are fields for which the corresponding sympy functions 
        # should be replaced with the primary (instead of auxiliary) petsc 
        # field value arrays. in this instance, we want to switch out 
        # `self.u` and `self.p` for their primary field 
        # petsc equivalents. without specifying this list, 
        # the aux field equivalents will be used instead, which 
        # will give incorrect results for non-linear problems.
        # note also that the order here is important.

        prim_field_list = [self.u,]
        cdef PtrContainer ext = getext(self.mesh, tuple(fns_residual), tuple(fns_jacobian), [x[1] for x in self.bcs], primary_field_list=prim_field_list, verbose=verbose)

        # set functions 
        self.dm.createDS()
        cdef DS ds = self.dm.getDS()
        PetscDSSetResidual(ds.ds, 0, ext.fns_residual[0], ext.fns_residual[1])
    
        # TODO: check if there's a significant performance overhead in passing in 
        # identically `zero` pointwise functions instead of setting to `NULL`
        PetscDSSetJacobian(ds.ds, 0, 0, ext.fns_jacobian[0], ext.fns_jacobian[1], ext.fns_jacobian[2], ext.fns_jacobian[3])
        
        # Note: this uses the label = 1 value for this BC (this could be a list of values in the label 
        #                                                  not just 1, and not just a single value)
        cdef int ind=1
        cdef int [::1] comps_view  # for numpy memory view
        cdef DM cdm = self.dm

        for index,bc in enumerate(self.bcs):
            comps_view = bc.components
            if uw.mpi.rank == 0 and self.verbose:
                print("Setting bc {} ({})".format(index, bc.type))
                print(" - components: {}".format(bc.components))
                print(" - boundary:   {}".format(bc.boundaries))
                print(" - fn:         {} ".format(bc.fn))

            for boundary in bc.boundaries:
                label = self.dm.getLabel(boundary)

                if not label:
                    if self.verbose == True:
                        print(f"Discarding bc {boundary} which has no corresponding mesh / dm label")
                    continue

                iset = label.getNonEmptyStratumValuesIS()
                if iset:
                    label_values = iset.getIndices()
                    if len(label_values > 0):
                        value = label_values[0]  # this is only one value in the label ... 
                        ind = value
                    else:
                        ind = -1

                # use type 5 bc for `DM_BC_ESSENTIAL_FIELD` enum
                # use type 6 bc for `DM_BC_NATURAL_FIELD` enum  (is this implemented for non-zero values ?)
                if bc.type == 'neumann':
                    bc_type = 6
                else:
                    bc_type = 5

                PetscDSAddBoundary_UW( cdm.dm, bc_type, str(boundary).encode('utf8'), str(boundary).encode('utf8'), 0, comps_view.shape[0], <const PetscInt *> &comps_view[0], <void (*)()>ext.fns_bcs[index], NULL, 1, <const PetscInt *> &ind, NULL)

        self.dm.setUp()

        self.dm.createClosureIndex(None)
        self.snes = PETSc.SNES().create(PETSc.COMM_WORLD)
        self.snes.setDM(self.dm)
        self.snes.setOptionsPrefix(self.petsc_options_prefix)
        self.snes.setFromOptions()
        cdef DM dm = self.dm
        DMPlexSetSNESLocalFEM(dm.dm, NULL, NULL, NULL)

        self.is_setup = True
        self.constitutive_model._solver_is_setup = True

    @timing.routine_timer_decorator
    def solve(self, 
              zero_init_guess: bool =True, 
              _force_setup:    bool =False ):
        """
        Generates solution to constructed system.

        Params
        ------
        zero_init_guess:
            If `True`, a zero initial guess will be used for the 
            system solution. Otherwise, the current values of `self.u` 
            and `self.p` will be used.
        """
        if (not self.is_setup) or _force_setup:
            self._setup_terms()

        gvec = self.dm.getGlobalVec()

        if not zero_init_guess:
            with self.mesh.access():
                self.dm.localToGlobal(self.u.vec, gvec)
        else:
            gvec.array[:] = 0.

        # Set quadrature to consistent value given by mesh quadrature.
        # self.mesh._align_quadratures()

        # Call `createDS()` on aux dm. This is necessary after the 
        # quadratures are set above, as it generates the tablatures 
        # from the quadratures (among other things no doubt). 
        # TODO: What does createDS do?
        # TODO: What are the implications of calling this every solve.

        self.mesh.dm.clearDS()
        self.mesh.dm.createDS()

        self.mesh.update_lvec()
        cdef DM dm = self.dm
        cdef Vec cmesh_lvec
        # PETSc == 3.16 introduced an explicit interface 
        # for setting the aux-vector which we'll use when available.
        cmesh_lvec = self.mesh.lvec
        ierr = DMSetAuxiliaryVec_UW(dm.dm, NULL, 0, 0, cmesh_lvec.vec); CHKERRQ(ierr)

        # solve
        self.snes.solve(None,gvec)

        lvec = self.dm.getLocalVec()
        cdef Vec clvec = lvec
        # Copy solution back into user facing variable
        with self.mesh.access(self.u):
            self.dm.globalToLocal(gvec, lvec)

            # add back boundaries.
            # Note that `DMPlexSNESComputeBoundaryFEM()` seems to need to use an lvec
            # derived from the system-dm (as opposed to the var.vec local vector), else 
            # failures can occur. 
            ierr = DMPlexSNESComputeBoundaryFEM(dm.dm, <void*>clvec.vec, NULL); CHKERRQ(ierr)
            self.u.vec.array[:] = lvec.array[:]

        self.dm.restoreLocalVec(lvec)
        self.dm.restoreGlobalVec(gvec)


### =================================

class SNES_Stokes_SaddlePt(Solver):
    r"""
    The `SNES_Stokes` solver class provides functionality for solving the *constrained* vector 
    conservation problem in the unknown $\mathbf{u}$ with the constraint parameter \(\mathrm{p}\):

    $$
    \nabla \cdot \color{Blue}{\mathbf{F}(\mathbf{u}, \mathrm{p}, \nabla \mathbf{u}, \nabla \mathbf{p}, \dot{\mathrm{u}}, \nabla\dot{\mathbf{u}})} - 
    \color{Green}{\mathbf{f} (\mathbf{u}, \mathrm{p}, \nabla \mathbf{u}, \nabla \mathbf{p}, \dot{\mathrm{u}}, \nabla\dot{\mathbf{u}})} = 0
    $$

    $$
    \mathrm{f}_p {(\mathbf{u}, \nabla \mathbf{u}, \dot{\mathrm{u}}, \nabla\dot{\mathbf{u}})} = 0
    $$

    where $\mathbf{f}$ is a source term for $u$, $\mathbf{F}$ is a flux term that relates
    the rate of change of $\mathbf{u}$ to the gradients $\nabla \mathbf{u}$, and $\dot{\mathbf{u}}$
    is the Lagrangian time-derivative of $\mathbf{u}$. $\mathrm{f}_p$ is the expression of the constraints 
    on $\mathbf{u}$ enforced by the parameter $\mathrm{p}$.

    $\mathbf{u}$ is a vector `underworld` `meshVariable` and $\mathbf{f}$, $\mathbf{F}$ and $\mathrm{F}_p$ 
    are arbitrary sympy expressions of the coodinate variables of the mesh and may include other
    `meshVariable` and `swarmVariable` objects. $\mathrm{p}$ is a scalar `underworld` `meshVariable`.

    This class is used as a base layer for building solvers which translate from the common
    physical conservation laws into this general mathematical form. 
    """

    instances = 0   # count how many of these there are in order to create unique private mesh variable ids

    @timing.routine_timer_decorator
    def __init__(self, 
                 mesh          : underworld3.discretisation.Mesh, 
                 velocityField : Optional[underworld3.discretisation.MeshVariable] =None,
                 pressureField : Optional[underworld3.discretisation.MeshVariable] =None,
                 solver_name   : Optional[str]                           ="stokes_pt_",
                 verbose       : Optional[str]                           =False,
                ):
     

        SNES_Stokes_SaddlePt.instances += 1
        self.name = solver_name
        self.mesh = mesh
        self.verbose = verbose
        
        # I expect the following to break for anyone who wants to name their solver _stokes__ etc etc (LM)

        if solver_name != "" and not solver_name.endswith("_"):
            self.petsc_options_prefix = solver_name+"_"
        else:
            self.petsc_options_prefix = solver_name

        # options = PETSc.Options()
        # options["dm_adaptor"]= "pragmatic"

        self.petsc_options = PETSc.Options(self.petsc_options_prefix)

        # Here we can set some defaults for this set of KSP / SNES solvers

        self._tolerance = 1.0e-4

        self.petsc_options["snes_converged_reason"] = None
        self.petsc_options["snes_monitor_short"] = None
        self.petsc_options["snes_rtol"] = self._tolerance
        self.petsc_options["ksp_rtol"]  = self._tolerance * 0.001
        self.petsc_options["ksp_atol"]  = self._tolerance * 1.0e-6

        self.petsc_options["pc_type"] = "fieldsplit"
        self.petsc_options["pc_fieldsplit_type"] = "schur"
        self.petsc_options["pc_fieldsplit_schur_fact_type"] = "full"     # diag is an alternative (quick/dirty)
        # self.petsc_options["pc_fieldsplit_schur_fact_type"] = "upper"  # upper is half way between these
        self.petsc_options["pc_fieldsplit_schur_precondition"] = "a11"   # despite what the docs say for saddle points

        self.petsc_options["pc_fieldsplit_diag_use_amat"] = None        
        self.petsc_options["pc_fieldsplit_off_diag_use_amat"] = None    
        self.petsc_options["pc_use_amat"] = None                         # Using this puts more pressure on the inner solve


        # Works / mostly quick
        self.petsc_options["fieldsplit_pressure_ksp_type"] = "fgmres"
        self.petsc_options["fieldsplit_pressure_ksp_rtol"]  = self._tolerance * 0.1
        self.petsc_options["fieldsplit_pressure_pc_type"] = "gasm"
        self.petsc_options["fieldsplit_pressure_pc_gasm_type"] = "basic"

        ## may be more robust but usually slower
        # self.petsc_options["fieldsplit_pressure_ksp_type"] = "fgmres"
        # self.petsc_options["fieldsplit_pressure_ksp_rtol"]  = self._tolerance * 0.1
        # self.petsc_options["fieldsplit_pressure_pc_type"] = "gamg"
        # self.petsc_options["fieldsplit_pressure_pc_gamg_type"] = "agg"
        # self.petsc_options["fieldsplit_pressure_pc_gamg_repartition"] = True

        self.petsc_options["fieldsplit_velocity_ksp_type"] = "cg"
        self.petsc_options["fieldsplit_velocity_ksp_rtol"]  = self._tolerance * 0.1
        self.petsc_options["fieldsplit_velocity_pc_type"]  = "gamg"
        self.petsc_options["fieldsplit_velocity_pc_gamg_type"]  = "agg"  
        self.petsc_options["fieldsplit_velocity_pc_gamg_repartition"]  = True  
        self.petsc_options["fieldsplit_velocity_pc_mg_type"]  = "additive"
        self.petsc_options["fieldsplit_velocity_pc_gamg_agg_nsmooths"] = 2
        self.petsc_options["fieldsplit_velocity_mg_levels_ksp_max_it"] = 3
        self.petsc_options["fieldsplit_velocity_mg_levels_ksp_converged_maxits"] = None

        self._u = velocityField
        self._p = pressureField

        # Create this dict
        self.fields = {}
        self.fields["pressure"] = self.p
        self.fields["velocity"] = self.u

        # Some other setup 

        self.mesh._equation_systems_register.append(self)

        # Build the DM / FE structures (should be done on remeshing, which is usually handled by the mesh register above)

        self._build_dm_and_mesh_discretisation()
        self._rebuild_after_mesh_update = self._build_dm_and_mesh_discretisation

        self.F0 = sympy.Matrix.zeros(1, self.mesh.dim) 
        self.F1 = sympy.Matrix.zeros(self.mesh.dim, self.mesh.dim)
        self.PF0 = sympy.Matrix.zeros(1, 1) 

        self.bcs = []
        self._constitutive_model = None
        self._saddle_preconditioner = None

        # Construct strainrate tensor for future usage.
        # Grab gradients, and let's switch out to sympy.Matrix notation
        # immediately as it is probably cleaner for this.
        N = mesh.N
  
        ## sympy.Matrix - gradient tensors 
        self._G = self._p.sym.jacobian(self.mesh.CoordinateSystem.N)
        self._L = self._u.sym.jacobian(self.mesh.CoordinateSystem.N) 

        # this attrib records if we need to re-setup
        self.is_setup = False
        super().__init__()

    @timing.routine_timer_decorator
    def _build_dm_and_mesh_discretisation(self):
        """
        Most of what is in the init phase that is not called by _setup_terms()
        """
        mesh = self.mesh
        u_degree = self.u.degree
        p_degree = self.p.degree
        p_continous = self.p.continuous

        if mesh.qdegree < u_degree: 
            print(f"Caution - the mesh quadrature ({mesh.qdegree})is lower")
            print(f"than {u_degree} which is required by the {self.name} solver")

        self.dm   = mesh.dm.clone()
        self.dm.createDS()
      
        options = PETSc.Options()
        options.setValue("{}_uprivate_petscspace_degree".format(self.petsc_options_prefix), u_degree) # for private variables
        self.petsc_fe_u = PETSc.FE().createDefault(mesh.dim, mesh.dim, mesh.isSimplex, mesh.qdegree, "{}_uprivate_".format(self.petsc_options_prefix), PETSc.COMM_WORLD)
        self.petsc_fe_u.setName("velocity")
        self.petsc_fe_u_id = self.dm.getNumFields() 
        self.dm.setField( self.petsc_fe_u_id, self.petsc_fe_u )

        options.setValue("{}_pprivate_petscspace_degree".format(self.petsc_options_prefix), p_degree)
        options.setValue("{}_pprivate_petscdualspace_lagrange_continuity".format(self.petsc_options_prefix), p_continous)
        options.setValue("{}_pprivate_petscdualspace_lagrange_node_endpoints".format(self.petsc_options_prefix), False)

        self.petsc_fe_p = PETSc.FE().createDefault(mesh.dim,    1, mesh.isSimplex, mesh.qdegree, "{}_pprivate_".format(self.petsc_options_prefix), PETSc.COMM_WORLD)
        self.petsc_fe_p.setName("pressure")
        self.petsc_fe_p_id = self.dm.getNumFields()
        self.dm.setField( self.petsc_fe_p_id, self.petsc_fe_p)

        self.is_setup = False

        return


    @property
    def tolerance(self):
        return self._tolerance
    @tolerance.setter
    def tolerance(self, value):
        self.is_setup = False # Need to make sure the snes machinery is set up consistently
        self._tolerance = value
        self.petsc_options["snes_rtol"] = self._tolerance
        self.petsc_options["ksp_rtol"] = self._tolerance * 1.0e-3
        self.petsc_options["ksp_atol"]  = self._tolerance * 1.0e-6
        self.petsc_options["fieldsplit_pressure_ksp_rtol"]  = self._tolerance * 0.1   # rule of thumb 
        self.petsc_options["fieldsplit_velocity_ksp_rtol"]  = self._tolerance * 0.1

    @property
    def PF0(self):
        return self._PF0
    @PF0.setter
    def PF0(self, value):
        self.is_setup = False
        # should add test here to make sure k is conformal
        self._PF0 = value

    @property
    def p(self):
        return self._p

    @property
    def saddle_preconditioner(self):
        return self._saddle_preconditioner

    @saddle_preconditioner.setter
    def saddle_preconditioner(self, function):
        self.is_setup = False
        self._saddle_preconditioner = function

    @timing.routine_timer_decorator
    def _setup_problem_description(self):

        # residual terms can be redefined by
        # writing your own version of this method

        # terms that become part of the weighted integral
        self._u_f0 = self.F0  # some_expression_u_f0(_V,_P. _L, _G)

        # Integration by parts into the stiffness matrix
        self._u_f1 = self.F1  # some_expression_u_f1(_V,_P, _L, _G)

        # rhs in the constraint (pressure) equations
        self._p_f0 = self.PF0  # some_expression_p_f0(_V,_P, _L, _G)

        return 

    def validate_solver(self):
        """Checks to see if the required properties have been set"""

        name = self.__class__.__name__

        if not isinstance(self.u, uw.discretisation.MeshVariable):
            print(f"Vector of unknowns required")
            print(f"{name}.u = uw.discretisation.MeshVariable(...)")
            raise RuntimeError("Unknowns: MeshVariable is required")       

        if not isinstance(self.p, uw.discretisation.MeshVariable):
            print(f"Vector of constraint unknowns required")
            print(f"{name}.p = uw.discretisation.MeshVariable(...)")
            raise RuntimeError("Constraint (Pressure): MeshVariable is required")       

        if not isinstance(self.constitutive_model, uw.systems.constitutive_models.Constitutive_Model):
            print(f"Constitutive model required")
            print(f"{name}.constitutive_model = uw.constitutive_models...")   
            raise RuntimeError("Constitutive Model is required")       

        return


    @timing.routine_timer_decorator
    def _setup_terms(self, verbose=False):
        import sympy

        dim  = self.mesh.dim
        cdim = self.mesh.cdim
        N = self.mesh.N

        sympy.core.cache.clear_cache()

        r = self.mesh.CoordinateSystem.N[0]

        # Re-clone the dm before rebuilding everything
        self._build_dm_and_mesh_discretisation() 
        
        # residual terms
        self._setup_problem_description()

        # Array form to work well with what is below
        # The basis functions are 3-vectors by default, even for 2D meshes, soooo ...
        F0  = sympy.Array(self._u_f0)  #.reshape(dim)
        F1  = sympy.Array(self._u_f1)  # .reshape(dim,dim)
        FP0 = sympy.Array(self._p_f0)# .reshape(1)

        # JIT compilation needs immutable, matrix input (not arrays)
        u_F0 = sympy.ImmutableDenseMatrix(F0)
        u_F1 = sympy.ImmutableDenseMatrix(F1)
        p_F0 = sympy.ImmutableDenseMatrix(FP0)

        fns_residual = [u_F0, u_F1, p_F0] 

        ## jacobian terms

        fns_jacobian = []

        ## NOTE PETSc and sympy require some re-ordering so that
        ## a `for element in Matrix:` loop produces functions 
        ## in the order that the PETSc jacobian routines expect. 
        ## This needs checking and completion. Especialy if we are 
        ## going to do this for arbitrary block systems.
        ## It's a bit easier for Stokes where P is a scalar field

        # This is needed to eliminate extra dims in the tensor
        U = sympy.Array(self._u.sym).reshape(dim)
        P = sympy.Array(self._p.sym).reshape(1)

        G0 = sympy.derive_by_array(F0, self._u.sym)
        G1 = sympy.derive_by_array(F0, self._L)  
        G2 = sympy.derive_by_array(F1, self._u.sym)
        G3 = sympy.derive_by_array(F1, self._L)

        # reorganise indices from sympy to petsc ordering / reshape to Matrix form
        # ijkl -> LJKI (hence 3120)
        # ij k -> KJ I (hence 210)
        # i jk -> J KI (hence 201)

        # The indices need to be interleaved, but for symmetric problems
        # there are lots of symmetries. This means we can find it hard to debug
        # the required permutation for a non-symmetric problem 
        permutation = (0,2,1,3) # ? same symmetry as I_ijkl ? # OK
        # permutation = (0,2,3,1) # ? same symmetry as I_ijkl ? # OK
        # permutation = (3,1,2,0) # ? same symmetry as I_ijkl ? # OK


        self._uu_G0 = sympy.ImmutableMatrix(sympy.permutedims(G0, permutation).reshape(dim,dim))
        self._uu_G1 = sympy.ImmutableMatrix(sympy.permutedims(G1, permutation).reshape(dim,dim*dim))
        self._uu_G2 = sympy.ImmutableMatrix(sympy.permutedims(G2, permutation).reshape(dim*dim,dim))   
        self._uu_G3 = sympy.ImmutableMatrix(sympy.permutedims(G3, permutation).reshape(dim*dim,dim*dim))

        fns_jacobian += [self._uu_G0, self._uu_G1, self._uu_G2, self._uu_G3]

        # U/P block (check permutations - hard to validate without a full collection of examples)

        G0 = sympy.derive_by_array(F0, self._p.sym)
        G1 = sympy.derive_by_array(F0, self._G)
        G2 = sympy.derive_by_array(F1, self._p.sym)
        G3 = sympy.derive_by_array(F1, self._G)

        self._up_G0 = sympy.ImmutableMatrix(G0.reshape(dim))  # zero in tests
        self._up_G1 = sympy.ImmutableMatrix(sympy.permutedims(G1, permutation).reshape(dim,dim))  # zero in stokes tests
        self._up_G2 = sympy.ImmutableMatrix(sympy.permutedims(G2, permutation).reshape(dim,dim))  # ?
        self._up_G3 = sympy.ImmutableMatrix(sympy.permutedims(G3, permutation).reshape(dim*dim,dim))  # zeros

        fns_jacobian += [self._up_G0, self._up_G1, self._up_G2, self._up_G3]

        # P/U block (check permutations)

        G0 = sympy.derive_by_array(FP0, self._u.sym)
        G1 = sympy.derive_by_array(FP0, self._L)
        # G2 = sympy.derive_by_array(FP1, U) # We don't have an FP1 ! 
        # G3 = sympy.derive_by_array(FP1, self._L)

        self._pu_G0 = sympy.ImmutableMatrix(G0.reshape(dim))  # non zero
        self._pu_G1 = sympy.ImmutableMatrix(G1.reshape(dim*dim))  # non-zero
        # self._pu_G2 = sympy.ImmutableMatrix(sympy.derive_by_array(FP1, self._p.sym).reshape(dim,dim))
        # self._pu_G3 = sympy.ImmutableMatrix(sympy.derive_by_array(FP1, self._G).reshape(dim,dim*2))

        # fns_jacobian += [self._pu_G0, self._pu_G1, self._pu_G2, self._pu_G3]
        fns_jacobian += [self._pu_G0, self._pu_G1]

        ## PP block is a preconditioner term, not auto-constructed

        if self.saddle_preconditioner is not None:
            self._pp_G0 = self.saddle_preconditioner
        else:
            self._pp_G0 = sympy.simplify(1 / self.constitutive_model.viscosity)

        fns_jacobian.append(self._pp_G0)

        # generate JIT code.
        # first, we must specify the primary fields.
        # these are fields for which the corresponding sympy functions 
        # should be replaced with the primary (instead of auxiliary) petsc 
        # field value arrays. in this instance, we want to switch out 
        # `self.u` and `self.p` for their primary field 
        # petsc equivalents. without specifying this list, 
        # the aux field equivalents will be used instead, which 
        # will give incorrect results for non-linear problems.
        # note also that the order here is important.

        if self.verbose and uw.mpi.rank==0:
            print(f"Stokes: Jacobians complete, now compile", flush=True)

        prim_field_list = [self.u, self.p]
        cdef PtrContainer ext = getext(self.mesh, tuple(fns_residual), tuple(fns_jacobian), [x[1] for x in self.bcs], primary_field_list=prim_field_list, verbose=verbose)
        # create indexes so that we don't rely on indices that can change
        i_res = {}
        for index,fn in enumerate(fns_residual):
            i_res[fn] = index
        i_jac = {}
        for index,fn in enumerate(fns_jacobian):
            i_jac[fn] = index

        if self.verbose and uw.mpi.rank==0:
            print(f"Stokes: Compilation complete, Now set residuals", flush=True)

        # set functions 

        self.dm.clearDS()
        self.dm.createDS()
        
        cdef DS ds = self.dm.getDS()
        PetscDSSetResidual(ds.ds, 0, ext.fns_residual[i_res[u_F0]], ext.fns_residual[i_res[u_F1]])
        PetscDSSetResidual(ds.ds, 1, ext.fns_residual[i_res[p_F0]],                          NULL)

        if self.verbose and uw.mpi.rank==0:
            print(f"Stokes:                      Now set jacobians", flush=True)

        
        # TODO: check if there's a significant performance overhead in passing in 
        # identically `zero` pointwise functions instead of setting to `NULL`
        PetscDSSetJacobian(              ds.ds, 0, 0, ext.fns_jacobian[i_jac[self._uu_G0]], ext.fns_jacobian[i_jac[self._uu_G1]], ext.fns_jacobian[i_jac[self._uu_G2]], ext.fns_jacobian[i_jac[self._uu_G3]])
        PetscDSSetJacobian(              ds.ds, 0, 1, ext.fns_jacobian[i_jac[self._up_G0]], ext.fns_jacobian[i_jac[self._up_G1]], ext.fns_jacobian[i_jac[self._up_G2]], ext.fns_jacobian[i_jac[self._up_G3]])
        PetscDSSetJacobian(              ds.ds, 1, 0, ext.fns_jacobian[i_jac[self._pu_G0]], ext.fns_jacobian[i_jac[self._pu_G1]],                                 NULL,                                 NULL)
        PetscDSSetJacobianPreconditioner(ds.ds, 0, 0, ext.fns_jacobian[i_jac[self._uu_G0]], ext.fns_jacobian[i_jac[self._uu_G1]], ext.fns_jacobian[i_jac[self._uu_G2]], ext.fns_jacobian[i_jac[self._uu_G3]])
        PetscDSSetJacobianPreconditioner(ds.ds, 0, 1, ext.fns_jacobian[i_jac[self._up_G0]], ext.fns_jacobian[i_jac[self._up_G1]], ext.fns_jacobian[i_jac[self._up_G2]], ext.fns_jacobian[i_jac[self._up_G3]])
        PetscDSSetJacobianPreconditioner(ds.ds, 1, 0, ext.fns_jacobian[i_jac[self._pu_G0]], ext.fns_jacobian[i_jac[self._pu_G1]],                                 NULL,                                 NULL)
        PetscDSSetJacobianPreconditioner(ds.ds, 1, 1, ext.fns_jacobian[i_jac[self._pp_G0]],                                 NULL,                                 NULL,                                 NULL)

        cdef int ind=1
        cdef int [::1] comps_view  # for numpy memory view
        cdef DM cdm = self.dm

        for index,bc in enumerate(self.bcs):
            comps_view = bc.components
            if uw.mpi.rank == 0 and self.verbose:
                print("Setting bc {} ({})".format(index, bc.type))
                print(" - components: {}".format(bc.components))
                print(" - boundary:   {}".format(bc.boundaries))
                print(" - fn:         {} ".format(bc.fn))

            for boundary in bc.boundaries:
                label = self.dm.getLabel(boundary)
                if not label:
                    if self.verbose == True:
                        print(f"Discarding bc {boundary} which has no corresponding mesh / dm label")
                    continue

                iset = label.getNonEmptyStratumValuesIS()
                if iset:
                    label_values = iset.getIndices()
                    if len(label_values > 0):
                        value = label_values[0]  # this is only one value in the label ... 
                        ind = value
                    else:
                        ind = -1

                # use type 5 bc for `DM_BC_ESSENTIAL_FIELD` enum
                # use type 6 bc for `DM_BC_NATURAL_FIELD` enum  (is this implemented for non-zero values ?)
                if bc.type == 'neumann':
                    bc_type = 6
                else:
                    bc_type = 5

                PetscDSAddBoundary_UW(cdm.dm, bc_type, str(boundary).encode('utf8'), str(boundary).encode('utf8'), 0, comps_view.shape[0], <const PetscInt *> &comps_view[0], <void (*)()>ext.fns_bcs[index], NULL, 1, <const PetscInt *> &ind, NULL)  
        
        self.dm.setUp()
        self.dm.createClosureIndex(None)
        self.snes = PETSc.SNES().create(PETSc.COMM_WORLD)
        self.snes.setDM(self.dm)
        self.snes.setOptionsPrefix(self.petsc_options_prefix)
        self.snes.setFromOptions()

        cdef DM c_dm = self.dm
        DMPlexSetSNESLocalFEM(c_dm.dm, NULL, NULL, NULL)

        # Setup subdms here too.
        # These will be used to copy back/forth SNES solutions
        # into user facing variables.
        
        names, isets, dms = self.dm.createFieldDecomposition()
        self._subdict = {}
        for index,name in enumerate(names):
            self._subdict[name] = (isets[index],dms[index])

        self.is_setup = True
        self.constitutive_model._solver_is_setup = True

    @timing.routine_timer_decorator
    def solve(self, 
              zero_init_guess: bool =True, 
              picard: int = 0,
              verbose=False,
              _force_setup:    bool =False, ):
        """
        Generates solution to constructed system.

        Params
        ------
        zero_init_guess:
            If `True`, a zero initial guess will be used for the 
            system solution. Otherwise, the current values of `self.u` 
            and `self.p` will be used.
        """

        if (not self.is_setup) or _force_setup:
            self._setup_terms(verbose)

        gvec = self.dm.getGlobalVec()
        gvec.setArray(0.0)

        if not zero_init_guess:
            with self.mesh.access():
                for name,var in self.fields.items():
                    sgvec = gvec.getSubVector(self._subdict[name][0])  # Get global subvec off solution gvec.
                    subdm   = self._subdict[name][1]                   # Get subdm corresponding to field
                    subdm.localToGlobal(var.vec,sgvec)                 # Copy variable data into gvec
                    gvec.restoreSubVector(self._subdict[name][0], sgvec) 

        # Call `createDS()` on aux dm. This is necessary after the 
        # quadratures are set above, as it generates the tablatures 
        # from the quadratures (among other things no doubt). 
        # TODO: What does createDS do?
        # TODO: What are the implications of calling this every solve.

        # self.mesh.dm.clearDS()
        # self.mesh.dm.createDS()

        self.mesh.update_lvec()
        self.dm.setAuxiliaryVec(self.mesh.lvec)

        # Picard solves if requested 

        tolerance = self.tolerance
        snes_type = self.snes.getType()

        if picard != 0:
            # low accuracy, picard-type iteration
            if picard > 0:
                self.tolerance = min(tolerance * 100.0, 0.01)
                self.snes.setType("nrichardson")
                self.petsc_options.setValue("snes_max_it", abs(picard))
                self.snes.setFromOptions()
                self.snes.solve(None, gvec)

            # low accuracy newtonls

            self.snes.setType("newtonls")
            self.tolerance = min(tolerance * 100.0, 0.01)
            self.petsc_options.setValue("snes_max_it", 50)
            self.snes.setFromOptions()
            self.snes.solve(None, gvec) 


        # Standard Newton solve 

        self.tolerance = tolerance
        self.snes.setType(snes_type)
        self.snes.setFromOptions()    
        self.snes.solve(None, gvec)
        self.snes.setType("newtonls")


        cdef Vec clvec
        cdef DM csdm
        # Copy solution back into user facing variables
        with self.mesh.access(self.p, self.u):
            for name,var in self.fields.items():
                ## print(f"{uw.mpi.rank}: Copy field {name} to user variables", flush=True)

                sgvec = gvec.getSubVector(self._subdict[name][0])  # Get global subvec off solution gvec.
                sdm   = self._subdict[name][1]                     # Get subdm corresponding to field.
                lvec = sdm.getLocalVec()                           # Get a local vector to push data into.
                sdm.globalToLocal(sgvec,lvec)                      # Do global to local into lvec
                sdm.localToGlobal(lvec, sgvec)
                gvec.restoreSubVector(self._subdict[name][0], sgvec) 

                # Put in boundaries values.
                # Note that `DMPlexSNESComputeBoundaryFEM()` seems to need to use an lvec
                # derived from the sub-dm (as opposed to the var.vec local vector), else 
                # failures can occur. 

                clvec = lvec
                csdm = sdm
                ierr = DMPlexSNESComputeBoundaryFEM(csdm.dm, <void*>clvec.vec, NULL); CHKERRQ(ierr)

                # Now copy into the user vec.
                var.vec.array[:] = lvec.array[:]
                sdm.restoreLocalVec(lvec)

        self.dm.restoreGlobalVec(gvec)

        return self.snes.getConvergedReason()
