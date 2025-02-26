# # Field Advection solver test - shear flow driven by a pre-defined, rigid body rotation in a disc
#
# This example uses the Swarm advection approach rather than SLCN

# +
import petsc4py
from petsc4py import PETSc

import underworld3 as uw
from underworld3.systems import Stokes
from underworld3 import function

import numpy as np

options = PETSc.Options()
# options["help"] = None
# options["pc_type"]  = "svd"
# options["dm_plex_check_all"] = None

# import os
# os.environ["SYMPY_USE_CACHE"]="no"

# options.getAll()


# +
import meshio

meshball = uw.meshing.Annulus(radiusOuter=1.0, radiusInner=0.5, cellSize=0.1, qdegree=3)
x,y = meshball.X
# -


v_soln = uw.discretisation.MeshVariable("U", meshball, meshball.dim, degree=2)
t_soln = uw.discretisation.MeshVariable("T", meshball, 1, degree=3)
t_0 = uw.discretisation.MeshVariable("T0", meshball, 1, degree=3)


swarm = uw.swarm.Swarm(mesh=meshball)
T1 = uw.swarm.SwarmVariable(r"T^{(-\Delta t)}", swarm, 1)
X1 = uw.swarm.SwarmVariable(r"X^{(-\Delta t)}", swarm, 2)
swarm.populate(fill_param=3)


with swarm.access():
    print(swarm.particle_coordinates.data.shape)

# check that the swarm variable works  as a continuous field as well 
T1.sym.jacobian(meshball.X)

# +
# Create adv_diff object

# Set some things
k = 1.0e-6
h = 0.1
t_i = 2.0
t_o = 1.0
r_i = 0.5
r_o = 1.0
delta_t = 1.0


# +
adv_diff = uw.systems.AdvDiffusionSwarm(
    meshball, u_Field=t_soln, u_Star_fn=T1.sym, 
    solver_name="adv_diff_swarms"  # not needed if coords is provided
)

adv_diff.constitutive_model = uw.systems.constitutive_models.DiffusionModel(meshball.dim)
adv_diff.constitutive_model.Parameters.diffusivity=k
# -


adv_diff._u_star_projector.uw_function

# +
# Create a density structure / buoyancy force
# gravity will vary linearly from zero at the centre
# of the sphere to (say) 1 at the surface

import sympy

radius_fn = sympy.sqrt(meshball.rvec.dot(meshball.rvec))  # normalise by outer radius if not 1.0
unit_rvec = meshball.rvec / (1.0e-10 + radius_fn)

# Some useful coordinate stuff

x, y = meshball.X
r, th = meshball.CoordinateSystem.xR

# Rigid body rotation v_theta = constant, v_r = 0.0

theta_dot = 2.0 * np.pi  # i.e one revolution in time 1.0
v_x = -r * theta_dot * sympy.sin(th)
v_y = r * theta_dot * sympy.cos(th)

with meshball.access(v_soln):
    v_soln.data[:, 0] = uw.function.evaluate(v_x, v_soln.coords)
    v_soln.data[:, 1] = uw.function.evaluate(v_y, v_soln.coords)

# +
# Define T boundary conditions via a sympy function

import sympy

abs_r = sympy.sqrt(meshball.rvec.dot(meshball.rvec))

init_t = sympy.exp(-30.0 * (meshball.N.x**2 + (meshball.N.y - 0.75) ** 2))

adv_diff.add_dirichlet_bc(0.0, "Lower")
adv_diff.add_dirichlet_bc(0.0, "Upper")

with meshball.access(t_0, t_soln):
    t_0.data[...] = uw.function.evaluate(init_t, t_0.coords).reshape(-1, 1)
    t_soln.data[...] = t_0.data[...]
    
with swarm.access(T1):
    T1.data[:,0] = uw.function.evaluate(t_soln.sym[0], swarm.particle_coordinates.data)


# +
# Validation - small timestep

delta_t = 0.0001
adv_diff.solve(timestep=delta_t)
# -


adv_diff.F1
adv_diff.constitutive_model.flux(adv_diff._L).T

# +
# check the mesh if in a notebook / serial


if uw.mpi.size == 1:

    import numpy as np
    import pyvista as pv
    import vtk

    pv.global_theme.background = "white"
    pv.global_theme.window_size = [750, 750]
    pv.global_theme.antialiasing = True
    pv.global_theme.jupyter_backend = "panel"
    pv.global_theme.smooth_shading = True
    pv.global_theme.camera["viewup"] = [0.0, 1.0, 0.0]
    pv.global_theme.camera["position"] = [0.0, 0.0, 10.0]

    meshball.vtk("tmp_ball.vtk")
    pvmesh = pv.read("tmp_ball.vtk")

    points = np.zeros((t_soln.coords.shape[0], 3))
    points[:, 0] = t_soln.coords[:, 0]
    points[:, 1] = t_soln.coords[:, 1]

    point_cloud = pv.PolyData(points)

    with meshball.access():
        point_cloud.point_data["T"] = t_0.data.copy()

    with meshball.access():
        usol = v_soln.data.copy()

    arrow_loc = np.zeros((v_soln.coords.shape[0], 3))
    arrow_loc[:, 0:2] = v_soln.coords[...]

    arrow_length = np.zeros((v_soln.coords.shape[0], 3))
    arrow_length[:, 0:2] = usol[...]

    pl = pv.Plotter()

    pl.add_arrows(arrow_loc, arrow_length, mag=0.0001, opacity=0.75)

    pl.add_points(point_cloud, cmap="coolwarm", render_points_as_spheres=False, point_size=10, opacity=0.66)

    pl.add_mesh(pvmesh, "Black", "wireframe", opacity=0.75)

    pl.remove_scalar_bar("T")
    pl.remove_scalar_bar("mag")

    pl.show()


# -


def plot_T_mesh(filename):

    if uw.mpi.size == 1:

        import numpy as np
        import pyvista as pv
        import vtk

        pv.global_theme.background = "white"
        pv.global_theme.window_size = [750, 750]
        pv.global_theme.antialiasing = True
        pv.global_theme.jupyter_backend = "pythreejs"
        pv.global_theme.smooth_shading = True
        pv.global_theme.camera["viewup"] = [0.0, 1.0, 0.0]
        pv.global_theme.camera["position"] = [0.0, 0.0, 5.0]

        meshball.vtk("tmp_ball.vtk")
        pvmesh = pv.read("tmp_ball.vtk")

        points = np.zeros((t_soln.coords.shape[0], 3))
        points[:, 0] = t_soln.coords[:, 0]
        points[:, 1] = t_soln.coords[:, 1]

        point_cloud = pv.PolyData(points)

        with meshball.access():
            point_cloud.point_data["T"] = t_soln.data.copy()

        with meshball.access():
            usol = v_soln.data.copy()

        arrow_loc = np.zeros((v_soln.coords.shape[0], 3))
        arrow_loc[:, 0:2] = v_soln.coords[...]

        arrow_length = np.zeros((v_soln.coords.shape[0], 3))
        arrow_length[:, 0:2] = usol[...]

        pl = pv.Plotter()

        pl.add_arrows(arrow_loc, arrow_length, mag=0.0001, opacity=0.75)

        pl.add_points(point_cloud, cmap="coolwarm", render_points_as_spheres=False, point_size=10, opacity=0.66)

        pl.add_mesh(pvmesh, "Black", "wireframe", opacity=0.75)

        pl.remove_scalar_bar("T")
        pl.remove_scalar_bar("mag")

        pl.screenshot(filename="{}.png".format(filename), window_size=(1280, 1280), return_img=False)

    # pl.show()


with meshball.access(t_0, t_soln):
    t_0.data[...] = uw.function.evaluate(init_t, t_0.coords).reshape(-1, 1)
    t_soln.data[...] = t_0.data[...]


# +
# Advection/diffusion model / update in time

delta_t = 0.05
adv_diff.k = 0.01
expt_name = "output/rotation_test_k_001"

plot_T_mesh(filename="{}_step_{}".format(expt_name, 0))

for step in range(1, 21):

    adv_diff.solve(timestep=delta_t) 
    
    # Update the swarm vallues
    with swarm.access(T1):
        T1.data[:,0] = uw.function.evaluate(t_soln.sym[0], swarm.particle_coordinates.data)
 
    # Update the swarm locations
    swarm.advection(v_soln.sym, delta_t=delta_t) 

    # stats then loop

    tstats = t_soln.stats()

    if uw.mpi.rank == 0:
        print("Timestep {}, dt {}".format(step, delta_t))
        print(tstats)

    plot_T_mesh(filename="{}_step_{}".format(expt_name, step))

    # savefile = "output_conv/convection_cylinder_{}_iter.h5".format(step)
    # meshball.save(savefile)
    # v_soln.save(savefile)
    # t_soln.save(savefile)
    # meshball.generate_xdmf(savefile)


# +
# check the mesh if in a notebook / serial


if uw.mpi.size == 1:

    import numpy as np
    import pyvista as pv
    import vtk

    pv.global_theme.background = "white"
    pv.global_theme.window_size = [750, 750]
    pv.global_theme.antialiasing = True
    pv.global_theme.jupyter_backend = "panel"
    pv.global_theme.smooth_shading = True
    pv.global_theme.camera["viewup"] = [0.0, 1.0, 0.0]
    pv.global_theme.camera["position"] = [0.0, 0.0, 5.0]

    meshball.vtk("tmp_ball.vtk")
    pvmesh = pv.read("tmp_ball.vtk")

    points = np.zeros((t_soln.coords.shape[0], 3))
    points[:, 0] = t_soln.coords[:, 0]
    points[:, 1] = t_soln.coords[:, 1]

    point_cloud = pv.PolyData(points)

    with meshball.access():
        point_cloud.point_data["T"] = t_soln.data
        point_cloud.point_data["dT"] = t_soln.data - t_0.data

    with meshball.access():
        usol = v_soln.data.copy()

    arrow_loc = np.zeros((v_soln.coords.shape[0], 3))
    arrow_loc[:, 0:2] = v_soln.coords[...]

    arrow_length = np.zeros((v_soln.coords.shape[0], 3))
    arrow_length[:, 0:2] = usol[...]

    pl = pv.Plotter()

    pl.add_arrows(arrow_loc, arrow_length, mag=0.0001, opacity=0.75)

    pl.add_points(
        point_cloud, cmap="coolwarm", scalars="T", render_points_as_spheres=False, point_size=10, opacity=0.66
    )

    pl.add_mesh(pvmesh, "Black", "wireframe", opacity=0.75)

    # pl.remove_scalar_bar("T")
    pl.remove_scalar_bar("mag")

    pl.show()

# +
# savefile = "output_conv/convection_cylinder.h5".format(step)
# meshball.save(savefile)
# v_soln.save(savefile)
# t_soln.save(savefile)
# meshball.generate_xdmf(savefile)
# -
adv_diff._f0
