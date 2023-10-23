import sympy
from sympy import sympify
import numpy as np

from typing import Optional, Callable, Union

import underworld3 as uw
from underworld3.systems import SNES_Scalar, SNES_Vector, SNES_Stokes_SaddlePt
from underworld3 import VarType
import underworld3.timing as timing


# class UW_Scalar_Temple(SNES_Scalar):


# class UW_Lagrangian_Helper:
#     """Mixin style ... add some functions to manage swarm updates etc"""

#     @property
#     def phi_star(self):
#         return "phi_star"

#     @property
#     def phi_star_star(self):
#         return "phi_star_star"


class SNES_Poisson(SNES_Scalar):
    r"""
    This class provides functionality for a discrete representation
    of the Poisson equation

    $$
    \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \boldsymbol\kappa \nabla u \Bigr]}_{\mathbf{F}}} =
            \color{Maroon}{\underbrace{\Bigl[ f \Bigl] }_{\mathbf{f}}}
    $$

    The term $\mathbf{F}$ relates the flux to gradients in the unknown $u$

    ## Properties

      - The unknown is $u$

      - The diffusivity tensor, $\kappa$ is provided by setting the `constitutive_model` property to
    one of the scalar `uw.systems.constitutive_models` classes and populating the parameters.
    It is usually a constant or a function of position / time and may also be non-linear
    or anisotropic.

      - $f$ is a volumetric source term
    """

    instances = 0

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        u_Field: uw.discretisation.MeshVariable = None,
        solver_name: str = "",
        verbose=False,
    ):
        ## Keep track

        SNES_Poisson.instances += 1

        if solver_name == "":
            solver_name = "Poisson_{}_".format(self.instances)

        ## Parent class will set up default values etc
        super().__init__(mesh, u_Field, solver_name, verbose)

        # Register the problem setup function
        self._setup_problem_description = self.poisson_problem_description

        # default values for properties
        self.f = sympy.Matrix.zeros(1, 1)

    ## This function is the one we will typically over-ride to build specific solvers.
    ## This example is a poisson-like problem with isotropic coefficients

    @timing.routine_timer_decorator
    def poisson_problem_description(self):

        # f1 residual term (weighted integration) - scalar function
        self._f0 = self.F0 - self.f

        # f1 residual term (integration by parts / gradients)
        # isotropic
        self._f1 = self.F1 + self.constitutive_model.flux.T

        return

    @property
    def f(self):
        return self._f

    @f.setter
    def f(self, value):
        self.is_setup = False
        self._f = sympy.Matrix((value,))


class SNES_Darcy(SNES_Scalar):
    r"""
    This class provides functionality for a discrete representation
    of the Groundwater flow equations

    $$
    \color{Green}{\underbrace{ \Bigl[  S_s \frac{\partial h}{\partial t} \Bigr]}_{\dot{\mathbf{f}}}} -
    \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \boldsymbol\kappa \nabla h  - \boldsymbol{s}\Bigr]}_{\mathbf{F}}} =
            \color{Maroon}{\underbrace{\Bigl[ W \Bigl] }_{\mathbf{f}}}
    $$

    The flux term, $\mathbf{F}$ relates the effective velocity to pressure gradients

    $$
    \boldsymbol{v} = \left( \boldsymbol\kappa \nabla h  - \boldsymbol{s} \right)
    $$

    The time-dependent term $\dot{\mathbf{f}}$ is not implemented in this version.

    ## Properties

      - The unknown is $h$, the hydraulic head

      - The permeability tensor, $\kappa$ is provided by setting the `constitutive_model` property to
    one of the scalar `uw.systems.constitutive_models` classes and populating the parameters.
    It is usually a constant or a function of position / time and may also be non-linear
    or anisotropic.

      - Volumetric sources for the pressure gradient are supplied through
        the $s$ property [e.g. $s = \rho g$ ]

      - $W$ is a pressure source term

      - $S_s$ is the specific storage coefficient

    ## Notes

      - The solver returns the primary field and also the Darcy flux term (the mean-flow velocity)

    """

    instances = 0

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        h_Field: uw.discretisation.MeshVariable,
        v_Field: uw.discretisation.MeshVariable,
        solver_name: str = "",
        verbose=False,
    ):
        ## Keep track

        SNES_Darcy.instances += 1

        if solver_name == "":
            solver_name = "Darcy_{}_".format(self.instances)

        ## Parent class will set up default values etc
        super().__init__(mesh, h_Field, solver_name, verbose)

        # Register the problem setup function
        self._setup_problem_description = self.darcy_problem_description

        # default values for properties
        self._f = 0.0
        self._k = 1.0

        self._s = sympy.Matrix.zeros(rows=1, cols=self.mesh.dim)
        self._s[1] = -1.0

        self._v = v_Field

        ## Set up the projection operator that
        ## solves the flow rate

        self._v_projector = uw.systems.solvers.SNES_Vector_Projection(self.mesh, self.v)

        # If we add smoothing, it should be small relative to actual diffusion (self.viscosity)
        self._v_projector.smoothing = 0.0

    ## This function is the one we will typically over-ride to build specific solvers.
    ## This example is a poisson-like problem with isotropic coefficients

    @timing.routine_timer_decorator
    def darcy_problem_description(self):

        # f1 residual term (weighted integration)
        self._f0 = self.F0 - self.f

        # f1 residual term (integration by parts / gradients)
        self._f1 = self.F1 + self.darcy_flux

        # Flow calculation
        self._v_projector.uw_function = -self.darcy_flux

        return

    @property
    def f(self):
        return self._f

    @f.setter
    def f(self, value):
        self.is_setup = False
        self._f = sympy.Matrix((value,))

    @property
    def W(self):
        return self._f

    @f.setter
    def W(self, value):
        self.is_setup = False
        self._f = sympy.Matrix((value,))

    @property
    def s(self):
        return self._s

    @s.setter
    def s(self, value):
        self.is_setup = False
        self._s = sympy.Matrix((value,))

    @property
    def darcy_flux(self):
        # flux = self.constitutive_model.flux(self._L - self.s).T
        flux = self.constitutive_model.flux.T
        return flux

    @property
    def v(self):
        return self._v

    @v.setter
    def v(self, value):
        self._v_projector.is_setup = False
        self._v = sympify(value)

    @timing.routine_timer_decorator
    def solve(
        self,
        zero_init_guess: bool = True,
        timestep: float = None,
        verbose: bool = False,
        _force_setup: bool = False,
    ):
        """
        Generates solution to constructed system.

        Params
        ------
        zero_init_guess:
            If `True`, a zero initial guess will be used for the
            system solution. Otherwise, the current values of `self.u` will be used.
        timestep:
            value used to evaluate inertial contribution
        """

        if (not self.is_setup) or _force_setup:
            self._setup_terms()
            self._setup_pointwise_functions(verbose)
            self._setup_discretisation(verbose)
            self._setup_solver(verbose)

        # Solve pressure

        super().solve(zero_init_guess, _force_setup)

        # Now solve flow field

        # self._v_projector.petsc_options[
        #     "snes_type"
        # ] = "newtontr"  ## newtonls seems to be problematic when the previous guess is available
        self._v_projector.petsc_options["snes_rtol"] = 1.0e-6
        self._v_projector.petsc_options.delValue("ksp_monitor")
        self._v_projector.solve(zero_init_guess)

        return

    @timing.routine_timer_decorator
    def _setup_terms(self):
        self._v_projector.uw_function = self.darcy_flux
        self._v_projector._setup_pointwise_functions()  # ._setup_terms()
        super()._setup_pointwise_functions()  # ._setup_terms()


## --------------------------------
## Stokes saddle point solver plus
## ancilliary functions - note that
## we need to update the description
## of the generic saddle pt solver
## to remove the Stokes-specific stuff
## --------------------------------


class SNES_Stokes(SNES_Stokes_SaddlePt):
    r"""
    This class provides functionality for a discrete representation
    of the Stokes flow equations assuming an incompressibility
    (or near-incompressibility) constraint.

    $$
    \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[
                    \boldsymbol{\tau} -  p \mathbf{I} \Bigr]}_{\mathbf{F}}} =
            \color{Maroon}{\underbrace{\Bigl[ \mathbf{f} \Bigl] }_{\mathbf{f}}}
    $$

    $$
    \underbrace{\Bigl[ \nabla \cdot \mathbf{u} \Bigr]}_{\mathbf{f}_p} = 0
    $$

    The flux term is a deviatoric stress ( $\boldsymbol{\tau}$ ) related to velocity gradients
      ( $\nabla \mathbf{u}$ ) through a viscosity tensor, $\eta$, and a volumetric (pressure) part $p$

    $$
        \mathbf{F}: \quad \boldsymbol{\tau} = \frac{\eta}{2}\left( \nabla \mathbf{u} + \nabla \mathbf{u}^T \right)
    $$

    The constraint equation, $\mathbf{f}_p = 0$ is incompressible flow by default but can be set
    to any function of the unknown  $\mathbf{u}$ and  $\nabla\cdot\mathbf{u}$

    ## Properties

      - The unknowns are velocities $\mathbf{u}$ and a pressure-like constraint paramter $\mathbf{p}$

      - The viscosity tensor, $\boldsymbol{\eta}$ is provided by setting the `constitutive_model` property to
    one of the scalar `uw.systems.constitutive_models` classes and populating the parameters.
    It is usually a constant or a function of position / time and may also be non-linear
    or anisotropic.

      - $\mathbf f$ is a volumetric source term (i.e. body forces)
      and is set by providing the `bodyforce` property.

      - An Augmented Lagrangian approach to application of the incompressibility
    constraint is to penalise incompressibility in the Stokes equation by adding
    $ \lambda \nabla \cdot \mathbf{u} $ when the weak form of the equations is constructed.
    (this is in addition to the constraint equation, unlike in the classical penalty method).
    This is activated by setting the `penalty` property to a non-zero floating point value which adds
    the term in the `sympy` expression.

      - A preconditioner is usually required for the saddle point system and this is provided
    though the `saddle_preconditioner` property. The default choice is $1/\eta$ for a scalar viscosity function.

    ## Notes

      - For problems with viscoelastic behaviour, the flux term contains the stress history as well as the
        stress and this term is a Lagrangian quantity that has to be tracked on a particle swarm.

      - The interpolation order of the `pressureField` variable is used to determine the integration order of
    the mixed finite element method and is usually lower than the order of the `velocityField` variable.

      - It is possible to set discontinuous pressure variables by setting the `p_continous` option to `False`

    """

    instances = 0

    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        velocityField: uw.discretisation.MeshVariable,
        pressureField: uw.discretisation.MeshVariable,
        solver_name: Optional[str] = "",
        verbose: Optional[bool] = False,
    ):
        SNES_Stokes.instances += 1

        if solver_name == "":
            solver_name = "Stokes_{}_".format(self.instances)

        super().__init__(mesh, velocityField, pressureField, solver_name, verbose)

        # User-facing operations are matrices / vectors by preference

        self._E = self.mesh.vector.strain_tensor(self._u.sym)
        self._Estar = None

        # scalar 2nd invariant (incompressible)
        self._Einv2 = sympy.sqrt((sympy.Matrix(self._E) ** 2).trace() / 2)
        self._penalty = 0.0
        self._constraints = sympy.Matrix(
            (self.div_u,)
        )  # by default, incompressibility constraint

        self._bodyforce = sympy.Matrix([[0] * self.mesh.dim])

        self._setup_problem_description = self.stokes_problem_description

        # this attrib records if we need to re-setup
        self.is_setup = False

        return

    @timing.routine_timer_decorator
    def stokes_problem_description(self):
        dim = self.mesh.dim

        # residual terms can be redefined here. We leave the
        # F0, F1, PF0 terms in place to allow injection of
        # additional terms. These are pre-defined to be zero

        # terms that become part of the weighted integral
        self._u_f0 = self.F0 - self.bodyforce
        # Integration by parts into the stiffness matrix (constitutive terms)
        self._u_f1 = sympy.simplify(
            self.F1 + self.stress + self.penalty * self.div_u * sympy.eye(dim)
        )

        # forces in the constraint (pressure) equations
        self._p_f0 = sympy.simplify(self.PF0 + sympy.Matrix((self.constraints)))

        return

    @property
    def strainrate(self):
        return sympy.Matrix(self._E)

    @property
    def strainrate_1d(self):
        return uw.maths.tensor.rank2_to_voigt(self.strainrate, self.mesh.dim)

    # Over-ride this one as required
    @property
    def strainrate_star(self):
        return None

    # provide the strain-rate history in symbolic form
    @strainrate_star.setter
    def strainrate_star(self, strain_rate_fn):
        self._is_setup = False
        symval = sympify(strain_rate_fn)
        self._Estar = symval

    @property
    def strainrate_star_1d(self):
        return uw.maths.tensor.rank2_to_voigt(self.strainrate_star, self.mesh.dim)

    # This should return standard viscous behaviour if strainrate_star is None
    @property
    def stress_deviator(self):
        return self.constitutive_model.flux  # strainrate, strain-rate history

    @property
    def stress_deviator_1d(self):
        return uw.maths.tensor.rank2_to_voigt(self.stress_deviator, self.mesh.dim)  ##

    @property
    def stress(self):
        return self.stress_deviator - sympy.eye(self.mesh.dim) * (self.p.sym[0])

    @property
    def stress_1d(self):
        return uw.maths.tensor.rank2_to_voigt(self.stress, self.mesh.dim)

    @property
    def div_u(self):
        E = self.strainrate
        divergence = E.trace()
        return divergence

    @property
    def constraints(self):
        return self._constraints

    @constraints.setter
    def constraints(self, constraints_matrix):
        self._is_setup = False
        symval = sympify(constraints_matrix)
        self._constraints = symval

    @property
    def bodyforce(self):
        return self._bodyforce

    @bodyforce.setter
    def bodyforce(self, value):
        self.is_setup = False
        self._bodyforce = self.mesh.vector.to_matrix(value)

    @property
    def saddle_preconditioner(self):
        return self._saddle_preconditioner

    @saddle_preconditioner.setter
    def saddle_preconditioner(self, value):
        self.is_setup = False
        symval = sympify(value)
        self._saddle_preconditioner = symval

    @property
    def penalty(self):
        return self._penalty

    @penalty.setter
    def penalty(self, value):
        self.is_setup = False
        symval = sympify(value)
        self._penalty = symval

    @timing.routine_timer_decorator
    def estimate_dt(self):
        """
        Calculates an appropriate advective timestep for the given
        mesh and velocity configuration.
        """
        # we'll want to do this on an element by element basis
        # for more general mesh

        # first let's extract a max global velocity magnitude
        import math

        with self.mesh.access():
            vel = self.u.data
            magvel_squared = vel[:, 0] ** 2 + vel[:, 1] ** 2
            if self.mesh.dim == 3:
                magvel_squared += vel[:, 2] ** 2

            max_magvel = math.sqrt(magvel_squared.max())

        from mpi4py import MPI

        comm = MPI.COMM_WORLD
        max_magvel_glob = comm.allreduce(max_magvel, op=MPI.MAX)

        min_dx = self.mesh.get_min_radius()
        return min_dx / max_magvel_glob


## --------------------------------
## Project from pointwise functions
## nodal point unknowns
## --------------------------------


class SNES_Projection(SNES_Scalar):
    r"""
    Solves $u = \tilde{f}$ where $\tilde{f}$ is a function that can be evaluated within an element and
    $u$ is a `meshVariable` with associated shape functions. Typically, the projection is used to obtain a
    continuous representation of a function that is not well defined at the mesh nodes. For example, functions of
    the spatial derivatives of one or more `meshVariable` (e.g. components of fluxes) can be mapped to continuous
    variables with a projection. More broadly it is a projection from one basis to another and its limitations should be
    evaluated within that context.

    The projection implemented by creating a solver for this problem

    $$
    \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \boldsymbol\alpha \nabla u \Bigr]}_{\mathbf{F}}} -
            \color{Maroon}{\underbrace{\Bigl[ u - \tilde{f} \Bigl] }_{\mathbf{f}}} = 0
    $$

    Where the term $\mathbf{F}$ provides a smoothing regularization. $\alpha$ can be zero.
    """

    instances = 0

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        u_Field: uw.discretisation.MeshVariable = None,
        solver_name: str = "",
        verbose=False,
    ):
        SNES_Projection.instances += 1

        if solver_name == "":
            solver_name = "SProj_{}_".format(self.instances)

        super().__init__(mesh, u_Field, solver_name, verbose)

        self._setup_problem_description = self.projection_problem_description
        self.is_setup = False
        self._smoothing = 0.0
        self._uw_weighting_function = 1.0
        self._constitutive_model = uw.systems.constitutive_models.Constitutive_Model(
            u_Field
        )

        return

    @timing.routine_timer_decorator
    def projection_problem_description(self):

        # residual terms - defines the problem:
        # solve for a best fit to the continuous mesh
        # variable given the values in self.function
        # F0 is left in place for the user to inject
        # non-linear constraints if required

        self._f0 = (
            self.F0 + (self.u.sym - self.uw_function) * self.uw_weighting_function
        )

        # F1 is left in the users control ... e.g to add other gradient constraints to the stiffness matrix

        self._f1 = self.F1 + self.smoothing * self.mesh.vector.gradient(self.u.sym)

        return

    @property
    def uw_function(self):
        return self._uw_function

    @uw_function.setter
    def uw_function(self, user_uw_function):
        self.is_setup = False
        self._uw_function = sympy.Matrix([user_uw_function])

    @property
    def smoothing(self):
        return self._smoothing

    @smoothing.setter
    def smoothing(self, smoothing_factor):
        self.is_setup = False
        self._smoothing = sympify(smoothing_factor)

    @property
    def uw_weighting_function(self):
        return self._uw_weighting_function

    @uw_weighting_function.setter
    def uw_weighting_function(self, user_uw_function):
        self.is_setup = False
        self._uw_weighting_function = user_uw_function


## --------------------------------
## Project from pointwise vector
## functions to nodal point unknowns
##
## We can add boundary constraints in the usual way (parent class handles this)
## We can add smoothing (which takes the form of a viscosity term)
## We could consider an incompressibility constraint ... or remove null spaces
## --------------------------------


class SNES_Vector_Projection(SNES_Vector):
    r"""
    Solves $\mathbf{u} = \tilde{\mathbf{f}}$ where $\tilde{\mathbf{f}}$ is a vector function that can be evaluated within an element and
    $\mathbf{u}$ is a vector `meshVariable` with associated shape functions. Typically, the projection is used to obtain a
    continuous representation of a function that is not well defined at the mesh nodes. For example, functions of
    the spatial derivatives of one or more `meshVariable` (e.g. components of fluxes) can be mapped to continuous
    variables with a projection. More broadly it is a projection from one basis to another and its limitations should be
    evaluated within that context.

    The projection is implemented by creating a solver for this problem

    $$
    \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \boldsymbol\alpha \nabla \mathbf{u} \Bigr]}_{\mathbf{F}}} -
            \color{Maroon}{\underbrace{\Bigl[ \mathbf{u} - \tilde{\mathbf{f}} \Bigl] }_{\mathbf{f}}} = 0
    $$

    Where the term $\mathbf{F}$ provides a smoothing regularization. $\alpha$ can be zero.
    """

    instances = 0

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        u_Field: uw.discretisation.MeshVariable = None,
        solver_name: str = "",
        verbose=False,
    ):
        SNES_Vector_Projection.instances += 1

        if solver_name == "":
            solver_name = "VProj{}_".format(self.instances)

        super().__init__(mesh, u_Field, u_Field.degree, solver_name, verbose)

        self._setup_problem_description = self.projection_problem_description
        self.is_setup = False
        self._smoothing = 0.0
        self._penalty = 0.0
        self._uw_weighting_function = 1.0
        self._constitutive_model = uw.systems.constitutive_models.Constitutive_Model(
            u_Field
        )

        return

    @timing.routine_timer_decorator
    def projection_problem_description(self):

        # residual terms - defines the problem:
        # solve for a best fit to the continuous mesh
        # variable given the values in self.function
        # F0 is left in place for the user to inject
        # non-linear constraints if required

        self._f0 = (
            self.F0 + (self.u.sym - self.uw_function) * self.uw_weighting_function
        )

        # F1 is left in the users control ... e.g to add other gradient constraints to the stiffness matrix

        E = 0.5 * (sympy.Matrix(self._L) + sympy.Matrix(self._L).T)  # ??
        self._f1 = (
            self.F1
            + self.smoothing * E
            + self.penalty
            * self.mesh.vector.divergence(self.u.sym)
            * sympy.eye(self.mesh.dim)
        )

        return

    @property
    def uw_function(self):
        return self._uw_function

    @uw_function.setter
    def uw_function(self, user_uw_function):
        self.is_setup = False
        self._uw_function = sympy.Matrix(user_uw_function)

    @property
    def smoothing(self):
        return self._smoothing

    @smoothing.setter
    def smoothing(self, smoothing_factor):
        self.is_setup = False
        self._smoothing = sympify(smoothing_factor)

    @property
    def penalty(self):
        return self._penalty

    @penalty.setter
    def penalty(self, value):
        self.is_setup = False
        symval = sympify(value)
        self._penalty = symval

    @property
    def uw_weighting_function(self):
        return self._uw_weighting_function

    @uw_weighting_function.setter
    def uw_weighting_function(self, user_uw_function):
        self.is_setup = False
        self._uw_weighting_function = user_uw_function


class SNES_Tensor_Projection(SNES_Projection):
    r"""
    Solves $\mathbf{u} = \tilde{\mathbf{f}}$ where $\tilde{\mathbf{f}}$ is a tensor-valued function that can be evaluated within an element and
    $\mathbf{u}$ is a tensor `meshVariable` with associated shape functions. Typically, the projection is used to obtain a
    continuous representation of a function that is not well defined at the mesh nodes. For example, functions of
    the spatial derivatives of one or more `meshVariable` (e.g. components of fluxes) can be mapped to continuous
    variables with a projection. More broadly it is a projection from one basis to another and its limitations should be
    evaluated within that context.

    The projection implemented by creating a solver for this problem

    $$
    \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \boldsymbol\alpha \nabla \mathbf{u} \Bigr]}_{\mathbf{F}}} -
            \color{Maroon}{\underbrace{\Bigl[ \mathbf{u} - \tilde{\mathbf{f}} \Bigl] }_{\mathbf{f}}} = 0
    $$

    Where the term $\mathbf{F}$ provides a smoothing regularization. $\alpha$ can be zero.

    Note: this is currently implemented component-wise as we do not have a native solver for tensor unknowns.

    """

    instances = 0

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        tensor_Field: uw.discretisation.MeshVariable = None,
        scalar_Field: uw.discretisation.MeshVariable = None,
        solver_name: str = "",
        verbose=False,
    ):
        SNES_Tensor_Projection.instances += 1

        if solver_name == "":
            solver_name = "TProj{}_".format(self.instances)

        self.t_field = tensor_Field

        super().__init__(
            mesh=mesh,
            u_Field=scalar_Field,
            solver_name=solver_name,
            verbose=verbose,
        )

        return

    ## Need to over-ride solve method to run over all components

    def solve(self):
        # Loop over the components of the tensor. If this is a symmetric
        # tensor, we'll usually be given the 1d form to prevent duplication

        # if self.t_field.sym_1d.shape != self.uw_function.shape:
        #     raise ValueError(
        #         "Tensor shapes for uw_function and MeshVariable are not the same"
        #     )

        symm = self.t_field.sym.is_symmetric

        for i in range(self.uw_function.shape[0]):
            for j in range(self.uw_function.shape[1]):
                if symm and j > i:
                    continue

                self.uw_scalar_function = sympy.Matrix([[self.uw_function[i, j]]])

                with self.mesh.access(self.u):
                    self.u.data[:, 0] = self.t_field[i, j].data[:]

                # solver for the scalar problem

                super().solve()

                with self.mesh.access(self.t_field):
                    self.t_field[i, j].data[:] = self.u.data[:, 0]

        # That might be all ...

    # This is re-defined so it uses uw_scalar_function

    @timing.routine_timer_decorator
    def projection_problem_description(self):

        # residual terms - defines the problem:
        # solve for a best fit to the continuous mesh
        # variable given the values in self.function
        # F0 is left in place for the user to inject
        # non-linear constraints if required

        self._f0 = (
            self.F0
            + (self.u.sym - self.uw_scalar_function) * self.uw_weighting_function
        )

        # F1 is left in the users control ... e.g to add other gradient constraints to the stiffness matrix

        self._f1 = self.F1 + self.smoothing * self.mesh.vector.gradient(self.u.sym)

        return

    @property
    def uw_scalar_function(self):
        return self._uw_scalar_function

    @uw_scalar_function.setter
    def uw_scalar_function(self, user_uw_function):
        self.is_setup = False
        self._uw_scalar_function = user_uw_function


## --------------------------------
## Project from pointwise vector
## functions to nodal point unknowns
##
## We can add boundary constraints in the usual way (parent class handles this)
## We can add smoothing (which takes the form of a viscosity term)
## Here we add an incompressibility constraint
## --------------------------------


#################################################
# Characteristics-based advection-diffusion
# solver based on SNES_Poisson and swarm-to-nodes
#
# Note that the solve() method has the swarm
# handler.
#################################################


class SNES_AdvectionDiffusion_SLCN(SNES_Poisson):
    r"""
    This class provides a solver for the scalar Advection-Diffusion equation using the characteristics based Semi-Lagrange Crank-Nicholson method
    which is described in Spiegelman & Katz, (2006).

    $$
    \color{Green}{\underbrace{ \Bigl[ \frac{\partial u}{\partial t} - \left( \mathbf{v} \cdot \nabla \right) u \Bigr]}_{\dot{\mathbf{f}}}} -
    \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \boldsymbol\kappa \nabla u \Bigr]}_{\mathbf{F}}} =
            \color{Maroon}{\underbrace{\Bigl[ f \Bigl] }_{\mathbf{f}}}
    $$

    The term $\mathbf{F}$ relates diffusive fluxes to gradients in the unknown $u$. The advective flux that results from having gradients along
    the direction of transport (given by the velocity vector field $\mathbf{v}$ ) are included in the $\dot{\mathbf{f}}$ term.

    The term $\dot{\mathbf{f}}$ involves upstream sampling to find the value $u^*$ which represents the value of $u$ at
    the points which later arrive at the nodal points of the mesh. This is achieved using a "hidden"
    swarm variable which is advected backwards from the nodal points automatically during the `solve` phase.

    ## Properties

      - The unknown is $u$.

      - The velocity field is $\mathbf{v}$ and is provided as a `sympy` function to allow operations such as time-averaging to be
        calculated in situ (e.g. `V_Field = v_solution.sym`) **NOTE: no it's not. Currently it is a MeshVariable** this is the desired behaviour though.

      - The diffusivity tensor, $\kappa$ is provided by setting the `constitutive_model` property to
        one of the scalar `uw.systems.constitutive_models` classes and populating the parameters.
        It is usually a constant or a function of position / time and may also be non-linear
        or anisotropic.

      - Volumetric sources of $u$ are specified using the $f$ property and can be any valid combination of `sympy` functions of position and
        `meshVariable` or `swarmVariable` types.

      - The `theta` property sets $\theta$, the parameter that tunes between backward Euler $\theta=1$, forward Euler $\theta=0$ and
        Crank-Nicholson $\theta=1/2$. The default is to use the Crank-Nicholson value.

    ## Notes

      - The solver requires relatively high order shape functions to accurately interpolate the history terms.
        Spiegelman & Katz recommend cubic or higher degree for $u$ but this is not checked.

    ## Reference

    Spiegelman, M., & Katz, R. F. (2006). A semi-Lagrangian Crank-Nicolson algorithm for the numerical solution
    of advection-diffusion problems. Geochemistry, Geophysics, Geosystems, 7(4). https://doi.org/10.1029/2005GC001073

    """

    def _object_viewer(self):
        from IPython.display import Latex, Markdown, display

        super()._object_viewer()

        ## feedback on this instance
        display(Latex(r"$\quad\mathrm{u} = $ " + self._u.sym._repr_latex_()))
        display(Latex(r"$\quad\mathbf{v} = $ " + self._V.sym._repr_latex_()))
        display(Latex(r"$\quad\Delta t = $ " + self.delta_t._repr_latex_()))
        display(Latex(r"$\quad\theta = $ " + self.theta._repr_latex_()))

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        u_Field: uw.discretisation.MeshVariable,
        V_fn: Union[
            uw.discretisation.MeshVariable, sympy.Basic
        ],  # Should be a sympy function
        theta: float = 0.5,
        order: int = 1,
        solver_name: str = "",
        restore_points_func: Callable = None,
        verbose=False,
    ):
        if solver_name == "":
            solver_name = "AdvDiff_slcn_{}_".format(self.instance_number)

        ## Parent class will set up default values and load u_Field into the solver
        super().__init__(mesh, u_Field, solver_name, verbose)

        if isinstance(V_fn, uw.discretisation._MeshVariable):
            self._V_fn = V_fn.sym
        else:
            self._V_fn = V_fn

        # These are unique to the advection solver
        self.delta_t = 0.0
        self.theta = theta
        self.is_setup = False

        self._DuDt = uw.swarm.SemiLagrange_Updater(
            self.mesh,
            self._u.sym,
            self._V_fn,
            vtype=uw.VarType.SCALAR,
            degree=self._u.degree,
            continuous=self._u.continuous,
            varsymbol=self._u.symbol,
            verbose=verbose,
            bcs=self.essential_bcs,
            order=order,
            smoothing=0.0,
        )

        self._DFDt = uw.swarm.SemiLagrange_Updater(
            self.mesh,
            sympy.Matrix(
                [[0] * self.mesh.dim]
            ),  # Actual function is not defined at this point
            self._V_fn,
            vtype=uw.VarType.VECTOR,
            degree=self._u.degree - 1,
            continuous=True,
            varsymbol=rf"{{F[ {self._u.symbol} ] }}",
            verbose=verbose,
            bcs=None,
            order=order,
            smoothing=0.0,
        )

        self.restore_points_to_domain_func = restore_points_func
        self._setup_problem_description = self.adv_diff_slcn_problem_description

        return

    def adv_diff_slcn_problem_description(self):

        # f0 residual term
        self._f0 = self.F0 - self.f + self.DuDt.bdf() / self.delta_t

        # f1 residual term

        # The F* term in here should be computed as the transported form of
        # the flux before the solve. The solver needs a self._Fstar in place of _Lstar

        self._f1 = self.F1 + self.DFDt.adams_moulton_flux()

        return

    @property
    def u(self):
        return self._u

    @property
    def V_fn(self):
        return self._V_fn

    @property
    def delta_t(self):
        return self._delta_t

    @delta_t.setter
    def delta_t(self, value):
        self.is_setup = False
        self._delta_t = sympify(value)

    @property
    def DuDt(self):
        return self._DuDt

    @property
    def DFDt(self):
        return self._DFDt

    # @property
    # def constitutive_model(self):
    #     return self._constitutive_model

    # @constitutive_model.setter
    # def constitutive_model(self, model):
    #     self._constitutive_model = model
    #     self._constitutive_model._solver_is_setup = False

    @property
    def theta(self):
        return self._theta

    @theta.setter
    def theta(self, value):
        self.is_setup = False
        self._theta = sympify(value)

    @timing.routine_timer_decorator
    def estimate_dt(self):
        """
        Calculates an appropriate advective timestep for the given
        mesh and diffusivity configuration.
        """

        if isinstance(self.constitutive_model.Parameters.diffusivity, sympy.Expr):
            k = uw.function.evaluate(
                sympy.sympify(self.constitutive_model.Parameters.diffusivity),
                self.mesh._centroids,
                self.mesh.N,
            )
            max_diffusivity = k.max()
        else:
            k = self.constitutive_model.Parameters.diffusivity
            max_diffusivity = k

        ### required modules
        from mpi4py import MPI

        # with self.mesh.access(self.k):
        #     ## get local max diff value
        #     max_diffusivity = self.k.data[:, 0].max()
        ### get the velocity values
        with self.mesh.access(self._V):
            vel = self._V.data

        ## get global max dif value
        comm = MPI.COMM_WORLD
        diffusivity_glob = comm.allreduce(max_diffusivity, op=MPI.MAX)

        ### get global velocity from velocity field
        max_magvel = np.linalg.norm(vel, axis=1).max()
        max_magvel_glob = comm.allreduce(max_magvel, op=MPI.MAX)

        ## get radius
        min_dx = self.mesh.get_min_radius()

        ## estimate dt of adv and diff components

        if max_magvel_glob == 0.0:
            dt_diff = (min_dx**2) / diffusivity_glob
            dt_estimate = dt_diff
        elif diffusivity_glob == 0.0:
            dt_adv = min_dx / max_magvel_glob
            dt_estimate = dt_adv
        else:
            dt_diff = (min_dx**2) / diffusivity_glob
            dt_adv = min_dx / max_magvel_glob
            dt_estimate = min(dt_diff, dt_adv)

        return dt_estimate

    @timing.routine_timer_decorator
    def solve(
        self,
        zero_init_guess: bool = True,
        timestep: float = None,
        _force_setup: bool = False,
        verbose=False,
    ):
        """
        Generates solution to constructed system.

        Params
        ------
        zero_init_guess:
            If `True`, a zero initial guess will be used for the
            system solution. Otherwise, the current values of `self.u` will be used.
        """

        if timestep is not None and timestep != self.delta_t:
            self.delta_t = timestep  # this will force an initialisation because the functions need to be updated

        if _force_setup:
            self.is_setup = False

        if not self.constitutive_model._solver_is_setup:
            self.is_setup = False
            self._DFDt.psi_fn = self.constitutive_model.flux.T

        if not self.is_setup:
            self._setup_pointwise_functions(verbose)
            self._setup_discretisation(verbose)
            self._setup_solver(verbose)

        # Update SemiLagrange Flux terms

        self.DuDt.update(timestep, verbose=verbose)
        self.DFDt.update(timestep, verbose=verbose)

        super().solve(zero_init_guess, _force_setup)

        self.is_setup = True
        self.constitutive_model._solver_is_setup = True

        return


#################################################
# Swarm-based advection-diffusion
# solver based on SNES_Poisson and swarm-variable
# projection
#
#################################################


class SNES_AdvectionDiffusion_Swarm(SNES_Poisson):
    r"""
    This class provides a solver for the scalar Advection-Diffusion equation which is similar to that
    used in the Semi-Lagrange Crank-Nicholson method (Spiegelman & Katz, 2006) but using a
    distributed sampling of upstream values taken from an arbitrary swarm variable.

    $$
    \color{Green}{\underbrace{ \Bigl[ \frac{\partial u}{\partial t} - \left( \mathbf{v} \cdot \nabla \right) u \Bigr]}_{\dot{\mathbf{f}}}} -
    \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \boldsymbol\kappa \nabla u \Bigr]}_{\mathbf{F}}} =
            \color{Maroon}{\underbrace{\Bigl[ f \Bigl] }_{\mathbf{f}}}
    $$

    The term $\mathbf{F}$ relates diffusive fluxes to gradients in the unknown $u$. The advective flux that results from having gradients along
    the direction of transport (given by the velocity vector field $\mathbf{v}$ ) are included in the $\dot{\mathbf{f}}$ term.

    The term $\dot{\mathbf{f}}$ involves upstream sampling to find the value $u^*$ which represents the value of $u$ at
    the beginning of the timestep. This is achieved using a `swarmVariable` that carries history information along the flow path.
    A dense sampling is required to achieve similar accuracy to the original SLCN approach but it allows the use of a single swarm
    for history tracking of variables with different interpolation order and for material tracking. The user is required to supply
    **and update** the swarmVariable representing $u^*$

    ## Properties

      - The unknown is $u$.

      - The velocity field is $\mathbf{v}$ and is provided as a `sympy` function to allow operations such as time-averaging to be
        calculated in situ (e.g. `V_Field = v_solution.sym`)

      - The history variable is $u^*$ and is provided in the form of a `sympy` function. It is the user's responsibility to keep this
        variable updated.

      - The diffusivity tensor, $\kappa$ is provided by setting the `constitutive_model` property to
        one of the scalar `uw.systems.constitutive_models` classes and populating the parameters.
        It is usually a constant or a function of position / time and may also be non-linear
        or anisotropic.

      - Volumetric sources of $u$ are specified using the $f$ property and can be any valid combination of `sympy` functions of position and
        `meshVariable` or `swarmVariable` types.

      - The `theta` property sets $\theta$, the parameter that tunes between backward Euler $\theta=1$, forward Euler $\theta=0$ and
        Crank-Nicholson $\theta=1/2$. The default is to use the Crank-Nicholson value.

    ## Notes

      - The solver requires relatively high order shape functions to accurately interpolate the history terms.
        Spiegelman & Katz recommend cubic or higher degree for $u$ but this is not checked.

    ## Reference

    Spiegelman, M., & Katz, R. F. (2006). A semi-Lagrangian Crank-Nicolson algorithm for the numerical solution
    of advection-diffusion problems. Geochemistry, Geophysics, Geosystems, 7(4). https://doi.org/10.1029/2005GC001073
    """

    instances = 0  # count how many of these there are in order to create unique private mesh variable ids

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        u_Field: uw.discretisation.MeshVariable = None,
        V_Field: uw.discretisation.MeshVariable = None,
        u_Star_fn=None,
        theta: float = 0.5,
        solver_name: str = "",
        restore_points_func: Callable = None,
        projection: bool = True,
        verbose: bool = False,
    ):
        self.instance = SNES_AdvectionDiffusion_Swarm.instances
        SNES_AdvectionDiffusion_Swarm.instances += 1

        if solver_name == "":
            solver_name = "AdvDiff_swarm_{}_".format(self.instances)

        ## Parent class will set up default values etc
        super().__init__(mesh, u_Field, solver_name, verbose)

        self.delta_t = 1.0
        self.theta = theta
        self.projection = projection
        self._u_star_raw_fn = u_Star_fn

        self._V = V_Field

        self.restore_points_to_domain_func = restore_points_func
        self._setup_problem_description = self.adv_diff_swarm_problem_description

        self.is_setup = False
        self.u_star_is_valid = False

        ### add a mesh variable to project diffusivity values to, may want to modify name and degree (?)
        # self.k = uw.discretisation.MeshVariable(
        #     f"k{self.instances}", self.mesh, num_components=1, degree=1
        # )

        if projection:
            # set up a projection solver

            self._u_star_projected = uw.discretisation.MeshVariable(
                r"u^{{*}}{}".format(self.instances), self.mesh, 1, degree=u_Field.degree
            )
            self._u_star_projector = uw.systems.solvers.SNES_Projection(
                self.mesh, self._u_star_projected
            )

            # If we add smoothing, it should be small relative to actual diffusion (self.k)
            self._u_star_projector.smoothing = 0.0
            self._u_star_projector.uw_function = self._u_star_raw_fn

        # if we want u_star to satisfy the bcs then this will need to be
        # a projection

        self._Lstar = self.mesh.vector.jacobian(self.u_star_fn)
        # sympy.derive_by_array(self.u_star_fn, self._X).reshape(self.mesh.dim)

        return

    def adv_diff_swarm_problem_description(self):

        # f0 residual term
        self._f0 = self.F0 - self.f + (self.u.sym - self.u_star_fn) / self.delta_t

        # f1 residual term
        self._f1 = (
            self.F1
            ## How are we going to deal with the theta term ??
            ## Going to have to pass in a history variable (or set)
            ## and state which Adams-Moulton order is required
            + self.theta * self.constitutive_model._q(self._L).T
            + (1.0 - self.theta) * self.constitutive_model._q(self._Lstar).T
        )

        return

    @property
    def u(self):
        return self._u

    @property
    def u_star_fn(self):
        if self.projection:
            return self._u_star_projected.sym
        else:
            return self._u_star_raw_fn

    @u_star_fn.setter
    def u_star_fn(self, u_star_fn):
        self.is_setup = False
        if self.projection:
            self._u_star_projector.is_setup = False
            self._u_star_projector.uw_function = u_star_fn

        self._u_star_raw_fn = u_star_fn

    @property
    def delta_t(self):
        return self._delta_t

    @delta_t.setter
    def delta_t(self, value):
        self.is_setup = False
        self._delta_t = sympify(value)

    @property
    def theta(self):
        return self._theta

    @theta.setter
    def theta(self, value):
        self.is_setup = False
        self._theta = sympify(value)

    @timing.routine_timer_decorator
    def estimate_dt(self):
        """
        Calculates an appropriate advective timestep for the given
        mesh and diffusivity configuration.
        """
        # ## update the diffusivity values
        # self.k_proj = uw.systems.Projection(self.mesh, self.k)
        # self.k_proj.uw_function = self.constitutive_model.Parameters.diffusivity
        # self.k_proj.smoothing = 0.0
        # self.k_proj.solve(_force_setup=True)

        if isinstance(self.constitutive_model.Parameters.diffusivity, sympy.Expr):
            k = uw.function.evaluate(
                sympy.sympify(self.constitutive_model.Parameters.diffusivity),
                self.mesh._centroids,
                self.mesh.N,
            )
            max_diffusivity = k.max()
        else:
            k = self.constitutive_model.Parameters.diffusivity
            max_diffusivity = k

        ### required modules
        from mpi4py import MPI

        # with self.mesh.access(self.k):
        #     ## get local max diff value
        #     max_diffusivity = self.k.data[:, 0].max()
        ### get the velocity values
        with self.mesh.access(self._V):
            vel = self._V.data

        ## get global max dif value
        comm = MPI.COMM_WORLD
        diffusivity_glob = comm.allreduce(max_diffusivity, op=MPI.MAX)

        ### get global velocity from velocity field
        max_magvel = np.linalg.norm(vel, axis=1).max()
        max_magvel_glob = comm.allreduce(max_magvel, op=MPI.MAX)

        ## get radius
        min_dx = self.mesh.get_min_radius()

        ## estimate dt of adv and diff components

        if max_magvel_glob == 0.0:
            dt_diff = (min_dx**2) / diffusivity_glob
            dt_estimate = dt_diff
        elif diffusivity_glob == 0.0:
            dt_adv = min_dx / max_magvel_glob
            dt_estimate = dt_adv
        else:
            dt_diff = (min_dx**2) / diffusivity_glob
            dt_adv = min_dx / max_magvel_glob
            dt_estimate = min(dt_diff, dt_adv)

        return dt_estimate

    @timing.routine_timer_decorator
    def solve(
        self,
        zero_init_guess: bool = True,
        timestep: float = None,
        _force_setup: bool = False,
        verbose: bool = False,
    ):
        """
        Generates solution to constructed system.

        Params
        ------
        zero_init_guess:
            If `True`, a zero initial guess will be used for the
            system solution. Otherwise, the current values of `self.u` will be used.
        """

        if timestep is not None and timestep != self.delta_t:
            self.delta_t = timestep  # this will force an initialisation because the functions need to be updated

        if _force_setup:
            self.is_setup = False

        if not self.is_setup:
            self._setup_terms()
            self._setup_pointwise_functions(verbose)
            self._setup_discretisation(verbose)
            self._setup_solver(verbose)

        # Make sure we update the projection of the swarm variable if requested

        if self.projection:
            self._u_star_projector.solve(zero_init_guess)

        # Over to you Poisson Solver

        super().solve(zero_init_guess, _force_setup)

        return

    @timing.routine_timer_decorator
    def _setup_terms(self):
        if self.projection:
            self._u_star_projector.bcs = self.bcs
            self._u_star_projector._setup_pointwise_functions()  # _setup_terms()

        super()._setup_pointwise_functions()  # ._setup_terms()


#################################################
# Swarm-based advection-diffusion
# solver based on SNES_Poisson and swarm-variable
# projection
#
#################################################


class SNES_NavierStokes_Swarm(SNES_Stokes):
    r"""
    This class provides a solver for the scalar Advection-Diffusion equation which is similar to that
    used in the Semi-Lagrange Crank-Nicholson method (Spiegelman & Katz, 2006) but using a
    distributed sampling of upstream values taken from an arbitrary swarm variable.

    $$
    \color{Green}{\underbrace{ \Bigl[ \frac{\partial \mathbf{u} }{\partial t} -
                                      \left( \mathbf{u} \cdot \nabla \right) \mathbf{u} \ \Bigr]}_{\dot{\mathbf{f}}}} -
        \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \frac{\boldsymbol{\eta}}{2} \left(
                    \nabla \mathbf{u} + \nabla \mathbf{u}^T \right) - p \mathbf{I} \Bigr]}_{\mathbf{F}}} =
            \color{Maroon}{\underbrace{\Bigl[ \mathbf{f} \Bigl] }_{\mathbf{f}}}
    $$

    The term $\mathbf{F}$ relates diffusive fluxes to gradients in the unknown $u$.
    The advective flux that results from having gradients along
    the direction of transport (given by the velocity vector field $\mathbf{v}$ )
    are included in the $\dot{\mathbf{f}}$ term.

    The term $\dot{\mathbf{f}}$ involves upstream sampling to find the value $u ^ { * }$ which represents the value of $u$ at
    the beginning of the timestep. This is achieved using a `swarmVariable` that carries history information along the flow path.
    A dense sampling is required to achieve similar accuracy to the original SLCN approach but it allows the use of a single swarm
    for history tracking of variables with different interpolation order and for material tracking. The user is required to supply
    **and update** the swarmVariable representing $u^{ * }$

    ## Properties

      - The unknown is $u$.

      - The velocity field is $\mathbf{v}$ and is provided as a `sympy` function to allow operations such as time-averaging to be
        calculated in situ (e.g. `V_Field = v_solution.sym`)

      - The history variable is $u^*$ and is provided in the form of a `sympy` function. It is the user's responsibility to keep this
        variable updated.

      - The diffusivity tensor, $\kappa$ is provided by setting the `constitutive_model` property to
        one of the scalar `uw.systems.constitutive_models` classes and populating the parameters.
        It is usually a constant or a function of position / time and may also be non-linear
        or anisotropic.

      - Volumetric sources of $u$ are specified using the $f$ property and can be any valid combination of `sympy` functions of position and
        `meshVariable` or `swarmVariable` types.

      - The `theta` property sets $\theta$, the parameter that tunes between backward Euler $\theta=1$, forward Euler $\theta=0$ and
        Crank-Nicholson $\theta=1/2$.

    ## Notes

      - The solver requires relatively high order shape functions to accurately interpolate the history terms.
        Spiegelman & Katz recommend cubic or higher degree for $u$ but this is not checked.

    ## Reference

    Spiegelman, M., & Katz, R. F. (2006). A semi-Lagrangian Crank-Nicolson algorithm for the numerical solution
    of advection-diffusion problems. Geochemistry, Geophysics, Geosystems, 7(4). https://doi.org/10.1029/2005GC001073

    """

    instances = 0  # count how many of these there are in order to create unique private mesh variable ids

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        velocityField: uw.discretisation.MeshVariable = None,
        pressureField: uw.discretisation.MeshVariable = None,
        velocityStar: Union[float, sympy.Function] = None,
        velocityStarStar: Union[float, sympy.Function] = None,
        stressStar: Union[float, sympy.Function] = None,
        stressStarStar: Union[float, sympy.Function] = None,
        rho: Optional[float] = 0.0,
        solver_name: Optional[str] = "",
        verbose: Optional[bool] = False,
        projection: Optional[bool] = False,
        restore_points_func: Callable = None,
    ):
        SNES_NavierStokes_Swarm.instances += 1

        if solver_name == "":
            solver_name = "NStokes_swarm_{}_".format(self.instances)

        self.delta_t = 1.0
        self.theta = 0.5
        self.projection = projection
        self.rho = rho
        self._u_star_raw_fn = velocityStar
        self._u_star_star_raw_fn = velocityStarStar
        self._stress_star_fn = stressStar
        self._stress_star_star_fn = stressStarStar

        ## Parent class will set up default values etc
        super().__init__(
            mesh,
            velocityField,
            pressureField,
            solver_name,
            verbose,
        )

        if projection:
            # set up a projection solver
            self._u_star_projected = uw.discretisation.MeshVariable(
                rf"u_star{self.instances}",
                self.mesh,
                self.mesh.dim,
                degree=self._u.degree,
                varsymbol=r"{U^{*}}",
            )
            self._u_star_projector = uw.systems.solvers.SNES_Vector_Projection(
                self.mesh, self._u_star_projected
            )

            options = self._u_star_projector.petsc_options
            options.delValue("ksp_monitor")

            # If we add smoothing, it should be small relative to actual diffusion (self.viscosity)
            self._u_star_projector.smoothing = 0.0
            self._u_star_projector.uw_function = self._u_star_raw_fn

            self._u_star_projector.add_dirichlet_bc(
                (self._u.sym[0],), "All_Boundaries", (0,)
            )
            self._u_star_projector.add_dirichlet_bc(
                (self._u.sym[1],), "All_Boundaries", (1,)
            )
            if self.mesh.dim == 3:
                self._u_star_projector.add_dirichlet_bc(
                    (self._u.sym[2],), "All_Boundaries", (2,)
                )

            if self._u_star_star_raw_fn is not None:
                # set up a projection solver for ustarstar
                self._u_star_star_projected = uw.discretisation.MeshVariable(
                    rf"u_star2{self.instances}",
                    self.mesh,
                    self.mesh.dim,
                    degree=self._u.degree,
                    varsymbol=r"{U^{**}}",
                )
                self._u_star_star_projector = uw.systems.solvers.SNES_Vector_Projection(
                    self.mesh, self._u_star_star_projected
                )

                options = self._u_star_star_projector.petsc_options
                options.delValue("ksp_monitor")

                # If we add smoothing, it should be small relative to actual diffusion (self.viscosity)
                self._u_star_star_projector.smoothing = 0.0
                self._u_star_star_projector.uw_function = self._u_star_star_raw_fn

                self._u_star_star_projector.add_dirichlet_bc(
                    (self._u.sym[0],), "All_Boundaries", (0,)
                )
                self._u_star_star_projector.add_dirichlet_bc(
                    (self._u.sym[1],), "All_Boundaries", (1,)
                )
                if self.mesh.dim == 3:
                    self._u_star_star_projector.add_dirichlet_bc(
                        (self._u.sym[2],), "All_Boundaries", (2,)
                    )

        self.restore_points_to_domain_func = restore_points_func
        self._setup_problem_description = self.navier_stokes_swarm_problem_description
        self.is_setup = False
        self.first_solve = True

        self._Estar = self.mesh.vector.strain_tensor(self.u_star_fn)

        return

    def navier_stokes_swarm_problem_description(self):
        dim = self.mesh.dim

        # terms that become part of the weighted integral
        self._u_f0 = (
            self.F0
            - 1.0 * self.bodyforce
            + self.rho * (self.u.sym - self.u_star_fn) / self.delta_t
        )

        if self.u_star_star_fn is not None:
            self._u_f0 = (
                self.F0
                - 1.0 * self.bodyforce
                + self.rho
                * (3 * self.u.sym - 4 * self.u_star_fn + self.u_star_star_fn)
                / (2 * self.delta_t)
            )

        # Three possibilities here:
        # 1. no stress history, use gradient of u*
        # 2. stress_star_fn is defined but stress_star_star is not
        # 3. stress_star_fn and stress_star_star_fn

        if self.u_star_star_fn is not None:
            self._u_f1 = (
                self.F1 + self.stress + self.penalty * self.div_u * sympy.eye(dim)
            )

        else:
            if self.stress_star_fn is None and self.stress_star_star_fn is None:
                self._u_f1 = (
                    self.F1
                    + self.stress / 2
                    + self.stress_star / 2
                    + self.penalty * self.div_u * sympy.eye(dim)
                )

            elif self.stress_star_star_fn is None:
                self._u_f1 = (
                    self.F1
                    + self.stress / 2
                    + self.stress_star_fn / 2
                    + self.penalty * self.div_u * sympy.eye(dim)
                )

            else:
                self._u_f1 = (
                    self.F1
                    + 5 * self.stress / 12
                    + 8 * self.stress_star_fn / 12
                    - self.stress_star_star_fn / 12
                    + self.penalty * self.div_u * sympy.eye(dim)
                )

        # forces in the constraint (pressure) equations
        self._p_f0 = self.PF0 + sympy.Matrix([self.div_u])

        return

    @property
    def u(self):
        return self._u

    @property
    def u_star_fn(self):
        if self.projection:
            return self._u_star_projected.sym
        else:
            return self._u_star_raw_fn

    @u_star_fn.setter
    def u_star_fn(self, uw_function):
        self.is_setup = False
        if self.projection:
            self._u_star_projector.is_setup = False
            self._u_star_projector.uw_function = uw_function

        self._u_star_raw_fn = uw_function

    @property
    def u_star_star_fn(self):
        if self._u_star_star_raw_fn is None:
            return None

        if self.projection:
            return self._u_star_star_projected.sym
        else:
            return self._u_star_star_raw_fn

    @u_star_star_fn.setter
    def u_star_star_fn(self, uw_function):
        self.is_setup = False
        if self.projection:
            self._u_star_star_projector.is_setup = False
            self._u_star_star_projector.uw_function = uw_function

        self._u_star_star_raw_fn = uw_function

    ## Can't really implement this way any more
    @property
    def stress_star_deviator(self):
        return self.constitutive_model._q(self.strainrate_star)

    @property
    def strainrate_star(self):
        return sympy.Matrix(self._Estar)

    @property
    def stress_star(self):
        return self.stress_star_deviator - sympy.eye(self.mesh.dim) * (self.p.sym[0])

    @property
    def stress_star_fn(self):
        if self._stress_star_fn is None:
            return None
        else:
            return self._stress_star_fn - sympy.eye(self.mesh.dim) * (self.p.sym[0])

    @property
    def stress_star_star_fn(self):
        if self._stress_star_star_fn is None:
            return None
        else:
            return self._stress_star_star_fn - sympy.eye(self.mesh.dim) * (
                self.p.sym[0]
            )

    @property
    def delta_t(self):
        return self._delta_t

    @delta_t.setter
    def delta_t(self, value):
        self.is_setup = False
        self._delta_t = sympify(value)

    @property
    def theta(self):
        return self._theta

    @theta.setter
    def theta(self, value):
        self.is_setup = False
        self._theta = sympify(value)

    @timing.routine_timer_decorator
    def solve(
        self,
        zero_init_guess: bool = True,
        timestep: float = None,
        verbose: bool = False,
        _force_u_star_projection: bool = False,
        _force_setup: bool = False,
    ):
        """
        Generates solution to constructed system.

        Params
        ------
        zero_init_guess:
            If `True`, a zero initial guess will be used for the
            system solution. Otherwise, the current values of `self.u` will be used.
        timestep:
            value used to evaluate inertial contribution
        """

        if timestep is not None and timestep != self.delta_t:
            self.delta_t = timestep  # this will force an initialisation because the functions need to be updated

        if (not self.is_setup) or _force_setup:
            self._setup_terms()
            self._setup_pointwise_functions(verbose)
            self._setup_discretisation(verbose)
            self._setup_solver(verbose)

        # Make sure we update the projection of the swarm variable if requested
        # But, this can break down on the first solve if there are constraints and bcs
        # (we might want to use v_star for checkpointing though)

        if self.projection and (not self.first_solve or _force_u_star_projection):
            self._u_star_projector.solve(zero_init_guess=False)
            if self.u_star_star_fn is not None:
                self._u_star_star_projector.solve(zero_init_guess=False)

        # Over to you Stokes Solver
        super().solve(zero_init_guess)
        self.first_solve = False

        return

    @timing.routine_timer_decorator
    def _setup_terms(self):
        if self.projection:
            # self._u_star_projector.bcs = self.bcs
            # self._u_star_projector.
            self._u_star_projector._setup_pointwise_functions()  # _setup_terms()

        super()._setup_pointwise_functions()  # ._setup_terms()


## Should we put in some TS style functionality here ... e.g. an Adam's moulton sympy wrapper or a BDF wrapper
## Perhaps ... but this can be explicitly written out in sympy for now while we have a look at the patterns
## and try to see what is fragile


class SNES_NavierStokes(SNES_Stokes):
    r"""
    This class provides a solver for the scalar Advection-Diffusion equation which is similar to that
    used in the Semi-Lagrange Crank-Nicholson method (Spiegelman & Katz, 2006) but using a
    distributed sampling of upstream values taken from an arbitrary swarm variable.

    $$
    \color{Green}{\underbrace{ \Bigl[ \frac{\partial \mathbf{u} }{\partial t} -
                                      \left( \mathbf{u} \cdot \nabla \right) \mathbf{u} \ \Bigr]}_{\dot{\mathbf{f}}}} -
        \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \frac{\boldsymbol{\eta}}{2} \left(
                    \nabla \mathbf{u} + \nabla \mathbf{u}^T \right) - p \mathbf{I} \Bigr]}_{\mathbf{F}}} =
            \color{Maroon}{\underbrace{\Bigl[ \mathbf{f} \Bigl] }_{\mathbf{f}}}
    $$

    The term $\mathbf{F}$ relates diffusive fluxes to gradients in the unknown $u$. The advective flux that results from having gradients along
    the direction of transport (given by the velocity vector field $\mathbf{v}$ ) are included in the $\dot{\mathbf{f}}$ term.

    The term $\dot{\mathbf{f}}$ involves upstream sampling to find the value $u^{ * }$ which represents the value of $u$ at
    the beginning of the timestep. This is achieved using a `swarmVariable` that carries history information along the flow path.
    A dense sampling is required to achieve similar accuracy to the original SLCN approach but it allows the use of a single swarm
    for history tracking of variables with different interpolation order and for material tracking. The user is required to supply
    **and update** the swarmVariable representing $u^{ * }$

    ## Properties

      - The unknown is $u$.

      - The velocity field is $\mathbf{v}$ and is provided as a `sympy` function to allow operations such as time-averaging to be
        calculated in situ (e.g. `V_Field = v_solution.sym`)

      - The history variable is $u^*$ and is provided in the form of a `sympy` function. It is the user's responsibility to keep this
        variable updated.

      - The diffusivity tensor, $\kappa$ is provided by setting the `constitutive_model` property to
        one of the scalar `uw.systems.constitutive_models` classes and populating the parameters.
        It is usually a constant or a function of position / time and may also be non-linear
        or anisotropic.

      - Volumetric sources of $u$ are specified using the $f$ property and can be any valid combination of `sympy` functions of position and
        `meshVariable` or `swarmVariable` types.

    ## Notes

      - The solver requires relatively high order shape functions to accurately interpolate the history terms.
        Spiegelman & Katz recommend cubic or higher degree for $u$ but this is not checked.

    ## Reference

    Spiegelman, M., & Katz, R. F. (2006). A semi-Lagrangian Crank-Nicolson algorithm for the numerical solution
    of advection-diffusion problems. Geochemistry, Geophysics, Geosystems, 7(4). https://doi.org/10.1029/2005GC001073

    """

    instances = 0  # count how many of these there are in order to create unique private mesh variable ids

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        velocityField: uw.discretisation.MeshVariable = None,
        pressureField: uw.discretisation.MeshVariable = None,
        DvDt: uw.swarm.Lagrangian_Updater = None,
        rho: Optional[float] = 0.0,
        solver_name: Optional[str] = "",
        verbose: Optional[bool] = False,
        restore_points_func: Callable = None,
    ):
        SNES_NavierStokes_Swarm.instances += 1

        if solver_name == "":
            solver_name = "NStokes_swarm_{}_".format(self.instances)

        self.delta_t = 1.0
        self.rho = rho
        self.DvDt = DvDt

        ## Stokes class will set up default values etc
        super().__init__(
            mesh,
            velocityField,
            pressureField,
            solver_name,
            verbose,
        )

        self.restore_points_to_domain_func = restore_points_func
        self._setup_problem_description = self.navier_stokes_swarm_problem_description
        self.is_setup = False
        self.first_solve = True

        self._Estar = self.mesh.vector.strain_tensor(DvDt.psi_star[0].sym)

        return

    def navier_stokes_swarm_problem_description(self):
        dim = self.mesh.dim

        # terms that become part of the weighted integral
        self._u_f0 = (
            self.F0 - 1.0 * self.bodyforce + self.rho * self.DvDt.bdf() / self._delta_t
        )

        self._u_f1 = (
            self.F1
            + self.stress / 2
            + self.stress_star / 2
            + self.penalty * self.div_u * sympy.eye(dim)
        )

        # forces in the constraint (pressure) equations
        self._p_f0 = self.PF0 + sympy.Matrix([self.div_u])

        return

    @property
    def u(self):
        return self._u

    @property
    def stress_star_deviator(self):
        return self.constitutive_model._q(self.strainrate_star)

    @property
    def strainrate_star(self):
        return sympy.Matrix(self._Estar)

    @property
    def stress_star(self):
        return self.stress_star_deviator - sympy.eye(self.mesh.dim) * (self.p.sym[0])

    @property
    def delta_t(self):
        return self._delta_t

    @delta_t.setter
    def delta_t(self, value):
        self.is_setup = False
        self._delta_t = sympify(value)

    @timing.routine_timer_decorator
    def solve(
        self,
        zero_init_guess: bool = True,
        timestep: float = None,
        verbose: bool = False,
        _force_setup: bool = False,
    ):
        """
        Generates solution to constructed system.

        Params
        ------
        zero_init_guess:
            If `True`, a zero initial guess will be used for the
            system solution. Otherwise, the current values of `self.u` will be used.
        timestep:
            value used to evaluate inertial contribution
        """

        ## If we try to change this timestep, we have to sync across all the D/Dt terms

        if timestep is not None and timestep != self.delta_t:
            self.delta_t = timestep  # this will force an initialisation because the functions need to be updated

        if (not self.is_setup) or _force_setup:
            # self._setup_terms()
            self._setup_pointwise_functions(verbose)
            self._setup_discretisation(verbose)
            self._setup_solver(verbose)

        # Over to you Stokes Solver
        super().solve(zero_init_guess)
        self.first_solve = False

        return


class SNES_NavierStokes_SLCN_old(SNES_Stokes):
    r"""
    This class provides a solver for the scalar Advection-Diffusion equation which is similar to that
    used in the Semi-Lagrange Crank-Nicholson method (Spiegelman & Katz, 2006) but using a
    distributed sampling of upstream values taken from an arbitrary swarm variable.

    $$
    \color{Green}{\underbrace{ \Bigl[ \frac{\partial \mathbf{u} }{\partial t} -
                                      \left( \mathbf{u} \cdot \nabla \right) \mathbf{u} \ \Bigr]}_{\dot{\mathbf{f}}}} -
        \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \frac{\boldsymbol{\eta}}{2} \left(
                    \nabla \mathbf{u} + \nabla \mathbf{u}^T \right) - p \mathbf{I} \Bigr]}_{\mathbf{F}}} =
            \color{Maroon}{\underbrace{\Bigl[ \mathbf{f} \Bigl] }_{\mathbf{f}}}
    $$

    The term $\mathbf{F}$ relates diffusive fluxes to gradients in the unknown $u$. The advective flux that results from having gradients along
    the direction of transport (given by the velocity vector field $\mathbf{v}$ ) are included in the $\dot{\mathbf{f}}$ term.

    The term $\dot{\mathbf{f}}$ involves upstream sampling to find the value $u^{ * }$ which represents the value of $u$ at
    the beginning of the timestep. This is achieved using a `swarmVariable` that carries history information along the flow path.
    A dense sampling is required to achieve similar accuracy to the original SLCN approach but it allows the use of a single swarm
    for history tracking of variables with different interpolation order and for material tracking. The user is required to supply
    **and update** the swarmVariable representing $u^{ * }$

    ## Properties

      - The unknown is $u$.

      - The velocity field is $\mathbf{v}$ and is provided as a `sympy` function to allow operations such as time-averaging to be
        calculated in situ (e.g. `V_Field = v_solution.sym`)

      - The history variable is $u^*$ and is provided in the form of a `sympy` function. It is the user's responsibility to keep this
        variable updated.

      - The diffusivity tensor, $\kappa$ is provided by setting the `constitutive_model` property to
        one of the scalar `uw.systems.constitutive_models` classes and populating the parameters.
        It is usually a constant or a function of position / time and may also be non-linear
        or anisotropic.

      - Volumetric sources of $u$ are specified using the $f$ property and can be any valid combination of `sympy` functions of position and
        `meshVariable` or `swarmVariable` types.

    ## Notes

      - The solver requires relatively high order shape functions to accurately interpolate the history terms.
        Spiegelman & Katz recommend cubic or higher degree for $u$ but this is not checked.

    ## Reference

    Spiegelman, M., & Katz, R. F. (2006). A semi-Lagrangian Crank-Nicolson algorithm for the numerical solution
    of advection-diffusion problems. Geochemistry, Geophysics, Geosystems, 7(4). https://doi.org/10.1029/2005GC001073

    """

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        velocityField: uw.discretisation.MeshVariable = None,
        pressureField: uw.discretisation.MeshVariable = None,
        rho: Optional[float] = 0.0,
        solver_name: Optional[str] = "",
        restore_points_func: Callable = None,
        verbose: Optional[bool] = False,
    ):
        if solver_name == "":
            solver_name = "NStokes_swarm_{}_".format(self.instance_number)

        self.delta_t = 1.0
        self.rho = rho
        # self.DvDt = DvDt

        ## Stokes class will set up default values etc
        super().__init__(
            mesh,
            velocityField,
            pressureField,
            solver_name,
            verbose,
        )

        self.restore_points_to_domain_func = restore_points_func
        self._setup_problem_description = self.navier_stokes_slcn_problem_description
        self.is_setup = False
        self.first_solve = True

        self._u_star = uw.discretisation.MeshVariable(
            f"u_star_slcn_{self.instance_number}",
            self.mesh,
            vtype=self._u.vtype,
            degree=self._u.degree,
            continuous=self._u.continuous,
            varsymbol=rf"{self._u.symbol}^{{*}}",
        )

        self._F_star = uw.discretisation.MeshVariable(
            f"F_star_slcn_{self.instance_number}",
            self.mesh,
            vtype=VarType.SYM_TENSOR,
            degree=self._u.degree - 1,
            continuous=False,
            varsymbol=r"F^{*}",
        )

        self._WorkVar = uw.discretisation.MeshVariable(
            f"W_star_slcn_{self.instance_number}",
            self.mesh,
            vtype=VarType.SCALAR,
            degree=self._u.degree - 1,
            continuous=False,
            varsymbol=r"W^{*}",
        )

        # Add the nodal point swarm which we'll use to track the characteristics
        nswarm_u = uw.swarm.NodalPointSwarm(self._u)
        self._nswarm_u = nswarm_u

        # Add the nodal point swarm which we'll use to track the characteristics
        nswarm_F = uw.swarm.NodalPointSwarm(self._F_star)
        self._nswarm_F = nswarm_F

        # Note, we really just need F*
        self._Lstar = self.mesh.vector.jacobian(self._u_star.sym)

        # set up a projection solver for _u_star_Field and for the flux term

        self._u_star_projection_solver = uw.systems.solvers.SNES_Vector_Projection(
            self.mesh, self._u_star, verbose=False
        )
        self._u_star_projection_solver.bcs = self.bcs
        self._u_star_projection_solver.smoothing = 1.0e-6

        self._F_star_projection_solver = uw.systems.solvers.SNES_Tensor_Projection(
            self.mesh, self._F_star, self._WorkVar, verbose=False
        )
        self._F_star_projection_solver.smoothing = 1.0e-6

        self.du_dt = uw.swarm.SemiLagrange_Updater(
            self.mesh,
            psi_fn=self._u.sym,
            V_fn=self._u.sym,
            vtype=self._u.vtype,
            degree=self._u.degree,
            continuous=self._u.continuous,
            order=1,
            smoothing=1.0,
            varsymbol=self._u.symbol,
            bcs=self.bcs,
        )

        return

    def navier_stokes_slcn_problem_description(self):
        dim = self.mesh.dim

        # Time derivatives should be managed as part of the semi-lagrange scheme (including
        # the order of accuracy / storage )

        self._u_f0 = (
            self.F0
            - 1.0 * self.bodyforce
            + self.rho * (self.u.sym - self._u_star.sym) / self.delta_t
        )

        # Flux history should be managed as part of the semi-lagrange scheme (including
        # the order of accuracy / storage )

        self._u_f1 = (
            self.F1
            + self.stress / 2
            + self._F_star.sym.T / 2
            + self.penalty * self.div_u * sympy.eye(dim)
        )

        # forces in the constraint (pressure) equations
        self._p_f0 = self.PF0 + sympy.Matrix([self.div_u])

        return

    @property
    def stress_star_deviator(self):
        return self.constitutive_model._q(self.strainrate_star)

    @property
    def strainrate_star(self):
        return sympy.Matrix(self._Estar)

    @property
    def stress_star(self):
        return self.stress_star_deviator - sympy.eye(self.mesh.dim) * (self.p.sym[0])

    @property
    def delta_t(self):
        return self._delta_t

    @delta_t.setter
    def delta_t(self, value):
        self.is_setup = False
        self._delta_t = sympify(value)

    @timing.routine_timer_decorator
    def solve(
        self,
        zero_init_guess: bool = True,
        timestep: float = None,
        verbose: bool = False,
        _force_setup: bool = False,
    ):
        """
        Generates solution to constructed system.

        Params
        ------
        zero_init_guess:
            If `True`, a zero initial guess will be used for the
            system solution. Otherwise, the current values of `self.u` will be used.
        timestep:
            value used to evaluate inertial contribution
        """

        ## If we try to change this timestep, we have to sync across all the D/Dt terms

        if timestep is not None and timestep != self.delta_t:
            self.delta_t = timestep  # this will force an initialisation because the functions need to be updated

        if (not self.is_setup) or _force_setup:
            # self._setup_terms()
            self._setup_pointwise_functions(verbose)
            self._setup_discretisation(verbose)
            self._setup_solver(verbose)

        ## Update the semi-lagrange terms here

        ####################################################################
        ## Update the U^* part - should be built in to a manager, of course ...

        nX0 = self._nswarm_u._X0
        nU1 = self._nswarm_u.swarmVariable

        with self._nswarm_u.access(nX0):
            nX0.data[...] = self._nswarm_u.data[...]

        # march nodes backwards along characteristics
        self._nswarm_u.advection(
            self._u.sym,
            -timestep,
            order=2,
            corrector=False,
            restore_points_to_domain_func=self.mesh.return_coords_to_bounds,
        )

        with self._nswarm_u.access(nU1):
            for d in range(self._u.num_components):
                nU1.data[:, d] = uw.function.evaluate(
                    self._u.sym_1d[d], self._nswarm_u.data
                )

        # restore coords (will call dm.migrate after context manager releases)
        with self._nswarm_u.access(self._nswarm_u.particle_coordinates):
            self._nswarm_u.data[...] = nX0.data[...]

        # Now project to the mesh using bc's to obtain u_star
        self._u_star_projection_solver.uw_function = nU1.sym
        self._u_star_projection_solver.solve()

        ######################################################################

        ## The transported Flux term

        nX0 = self._nswarm_F._X0
        nF1 = self._nswarm_F.swarmVariable

        # The flux has to be calculated before we can sample the values
        self._F_star_projection_solver.uw_function = self.constitutive_model.flux.T
        self._F_star_projection_solver.solve()

        with self._nswarm_F.access(nX0):
            nX0.data[...] = self._nswarm_F.data[...]

        # Mid point method to find launch points (T*)
        self._nswarm_F.advection(
            self._u.sym,
            -timestep,
            order=2,
            corrector=False,
            restore_points_to_domain_func=self.mesh.return_coords_to_bounds,
        )

        with self._nswarm_F.access(nF1):
            for d in range(self.mesh.dim):
                nF1.data[:, d] = uw.function.evaluate(
                    self._F_star.sym[d], self._nswarm_F.data
                )  # .reshape(-1, 1)

        # restore coords (will call dm.migrate after context manager releases)
        with self._nswarm_F.access(self._nswarm_F.particle_coordinates):
            self._nswarm_F.data[...] = nX0.data[...]

        # This may not be required if we have no bcs to apply
        self._F_star_projection_solver.uw_function = nF1.sym
        self._F_star_projection_solver.solve()

        # Over to you Stokes Solver
        super().solve(zero_init_guess)
        self.first_solve = False

        return


class SNES_NavierStokes_SLCN(SNES_Stokes):
    r"""
    This class provides a solver for the Navier-Stokes (vector Advection-Diffusion) equation which is similar to that
    used in the Semi-Lagrange Crank-Nicholson method (Spiegelman & Katz, 2006) but using a
    distributed sampling of upstream values taken from an arbitrary swarm variable.

    $$
    \color{Green}{\underbrace{ \Bigl[ \frac{\partial \mathbf{u} }{\partial t} -
                                      \left( \mathbf{u} \cdot \nabla \right) \mathbf{u} \ \Bigr]}_{\dot{\mathbf{f}}}} -
        \nabla \cdot
            \color{Blue}{\underbrace{\Bigl[ \frac{\boldsymbol{\eta}}{2} \left(
                    \nabla \mathbf{u} + \nabla \mathbf{u}^T \right) - p \mathbf{I} \Bigr]}_{\mathbf{F}}} =
            \color{Maroon}{\underbrace{\Bigl[ \mathbf{f} \Bigl] }_{\mathbf{f}}}
    $$

    The term $\mathbf{F}$ relates diffusive fluxes to gradients in the unknown $u$. The advective flux that results from having gradients along
    the direction of transport (given by the velocity vector field $\mathbf{v}$ ) are included in the $\dot{\mathbf{f}}$ term.

    The term $\dot{\mathbf{f}}$ involves upstream sampling to find the value $u^{ * }$ which represents the value of $u$ at
    the beginning of the timestep. This is achieved using a `swarmVariable` that carries history information along the flow path.
    A dense sampling is required to achieve similar accuracy to the original SLCN approach but it allows the use of a single swarm
    for history tracking of variables with different interpolation order and for material tracking. The user is required to supply
    **and update** the swarmVariable representing $u^{ * }$

    ## Properties

      - The unknown is $u$.

      - The velocity field is $\mathbf{v}$ and is provided as a `sympy` function to allow operations such as time-averaging to be
        calculated in situ (e.g. `V_Field = v_solution.sym`)

      - The history variable is $u^*$ and is provided in the form of a `sympy` function. It is the user's responsibility to keep this
        variable updated.

      - The diffusivity tensor, $\kappa$ is provided by setting the `constitutive_model` property to
        one of the scalar `uw.systems.constitutive_models` classes and populating the parameters.
        It is usually a constant or a function of position / time and may also be non-linear
        or anisotropic.

      - Volumetric sources of $u$ are specified using the $f$ property and can be any valid combination of `sympy` functions of position and
        `meshVariable` or `swarmVariable` types.

    ## Notes

      - The solver requires relatively high order shape functions to accurately interpolate the history terms.
        Spiegelman & Katz recommend cubic or higher degree for $u$ but this is not checked.

    ## Reference

    Spiegelman, M., & Katz, R. F. (2006). A semi-Lagrangian Crank-Nicolson algorithm for the numerical solution
    of advection-diffusion problems. Geochemistry, Geophysics, Geosystems, 7(4). https://doi.org/10.1029/2005GC001073
    """

    def _object_viewer(self):
        from IPython.display import Latex, Markdown, display

        super()._object_viewer()

        ## feedback on this instance
        display(Latex(r"$\quad\mathrm{u} = $ " + self._u.sym._repr_latex_()))
        display(Latex(r"$\quad\mathbf{p} = $ " + self._p.sym._repr_latex_()))
        display(Latex(r"$\quad\Delta t = $ " + self.delta_t._repr_latex_()))
        display(Latex(rf"$\quad\rho = $ {self.rho}"))

    @timing.routine_timer_decorator
    def __init__(
        self,
        mesh: uw.discretisation.Mesh,
        velocityField: uw.discretisation.MeshVariable,
        pressureField: uw.discretisation.MeshVariable,
        rho: Optional[float] = 0.0,
        solver_name: Optional[str] = "",
        restore_points_func: Callable = None,
        verbose: Optional[bool] = False,
        order: Optional[int] = 1,
    ):
        if solver_name == "":
            solver_name = "NavierStokes_slcn_{}_".format(self.instance_number)

        ## Parent class will set up default values and load u_Field into the solver
        super().__init__(mesh, velocityField, pressureField, solver_name, verbose)

        # These are unique to the advection solver
        self.delta_t = sympy.oo
        self.is_setup = False
        self.rho = rho
        self._first_solve = True

        self._DuDt = uw.swarm.SemiLagrange_Updater(
            self.mesh,
            self._u.sym,
            self._u.sym,
            vtype=uw.VarType.VECTOR,
            degree=self._u.degree,
            continuous=self._u.continuous,
            varsymbol=self._u.symbol,
            verbose=verbose,
            bcs=self.essential_bcs,
            order=order,
            smoothing=0.0,
        )

        self._DFDt = uw.swarm.SemiLagrange_Updater(
            self.mesh,
            sympy.Matrix.zeros(self.mesh.dim, self.mesh.dim),
            self._u.sym,
            vtype=uw.VarType.SYM_TENSOR,
            degree=self._u.degree - 1,
            continuous=True,
            varsymbol=rf"{{F[ {self._u.symbol} ] }}",
            verbose=verbose,
            bcs=None,
            order=order,
            smoothing=0.0,
        )

        self.restore_points_to_domain_func = restore_points_func
        self._setup_problem_description = self.navier_stokes_slcn_problem_description

        return

    def navier_stokes_slcn_problem_description(self):

        # f0 residual term
        self._u_f0 = (
            self.F0 - self.bodyforce + self.rho * self.DuDt.bdf() / self.delta_t
        )

        # f1 residual term
        self._u_f1 = (
            self.F1
            + self.DFDt.adams_moulton_flux()
            - sympy.eye(self.mesh.dim) * (self.p.sym[0])
        )
        self._p_f0 = sympy.simplify(self.PF0 + sympy.Matrix((self.constraints)))

        return

    @property
    def delta_t(self):
        return self._delta_t

    @delta_t.setter
    def delta_t(self, value):
        self.is_setup = False
        self._delta_t = sympify(value)

    @property
    def DuDt(self):
        return self._DuDt

    @property
    def DFDt(self):
        return self._DFDt

    # @timing.routine_timer_decorator
    # def estimate_dt(self):
    #     """
    #     Calculates an appropriate advective timestep for the given
    #     mesh and diffusivity configuration.
    #     """

    #     if isinstance(self.constitutive_model.Parameters.diffusivity, sympy.Expr):
    #         k = uw.function.evaluate(
    #             sympy.sympify(self.constitutive_model.Parameters.diffusivity),
    #             self.mesh._centroids,
    #             self.mesh.N,
    #         )
    #         max_diffusivity = k.max()
    #     else:
    #         k = self.constitutive_model.Parameters.diffusivity
    #         max_diffusivity = k

    #     ### required modules
    #     import math
    #     from mpi4py import MPI

    #     # with self.mesh.access(self.k):
    #     #     ## get local max diff value
    #     #     max_diffusivity = self.k.data[:, 0].max()
    #     ### get the velocity values
    #     with self.mesh.access(self._V):
    #         vel = self._V.data

    #     ## get global max dif value
    #     comm = MPI.COMM_WORLD
    #     diffusivity_glob = comm.allreduce(max_diffusivity, op=MPI.MAX)

    #     ### get global velocity from velocity field
    #     max_magvel = np.linalg.norm(vel, axis=1).max()
    #     max_magvel_glob = comm.allreduce(max_magvel, op=MPI.MAX)

    #     ## get radius
    #     min_dx = self.mesh.get_min_radius()

    #     ## estimate dt of adv and diff components

    #     if max_magvel_glob == 0.0:
    #         dt_diff = (min_dx**2) / diffusivity_glob
    #         dt_estimate = dt_diff
    #     elif diffusivity_glob == 0.0:
    #         dt_adv = min_dx / max_magvel_glob
    #         dt_estimate = dt_adv
    #     else:
    #         dt_diff = (min_dx**2) / diffusivity_glob
    #         dt_adv = min_dx / max_magvel_glob
    #         dt_estimate = min(dt_diff, dt_adv)

    #     return dt_estimate

    @timing.routine_timer_decorator
    def solve(
        self,
        zero_init_guess: bool = True,
        timestep: float = None,
        _force_setup: bool = False,
        verbose=False,
    ):
        """
        Generates solution to constructed system.

        Params
        ------
        zero_init_guess:
            If `True`, a zero initial guess will be used for the
            system solution. Otherwise, the current values of `self.u` will be used.
        """

        if timestep is not None and timestep != self.delta_t:
            self.delta_t = timestep  # this will force an initialisation because the functions need to be updated

        if _force_setup:
            self.is_setup = False

        if not self.constitutive_model._solver_is_setup:
            self.is_setup = False
            self._DFDt.psi_fn = self.constitutive_model.flux.T

        if not self.is_setup:
            self._setup_pointwise_functions(verbose)
            self._setup_discretisation(verbose)
            self._setup_solver(verbose)

        # Update SemiLagrange Flux terms
        self.DuDt.update(timestep, verbose=verbose)
        self.DFDt.update(timestep, verbose=verbose)

        super().solve(
            zero_init_guess,
            _force_setup=_force_setup,
            verbose=verbose,
            picard=0,
        )

        self.is_setup = True
        self.constitutive_model._solver_is_setup = True

        return
