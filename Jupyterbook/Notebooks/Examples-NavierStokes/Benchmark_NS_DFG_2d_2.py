# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: light
#       format_version: '1.5'
#       jupytext_version: 1.14.1
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---


# # Navier Stokes test: flow around a circular inclusion (2D)
#
# http://www.mathematik.tu-dortmund.de/~featflow/en/benchmarks/cfdbenchmarking/flow/dfg_benchmark2_re100.html
#
# No slip conditions
#
# ![](http://www.mathematik.tu-dortmund.de/~featflow/media/dfg_bench1_2d/geometry.png)
#
# This is the same as benchmark 1 but with higher flow rate. Vortex shedding seems to be hard to
# provoke with this formulation but an good kick at the start of the simulation seems to get things moving.
#
# Note ...
#
# In this benchmark, I have scaled $\rho = 1000$ and $\nu = 1.0$ as otherwise it fails to converge.
# I can't see why that should be but somewhere I must have assumed an absolute value of viscosity
# being close to one or not appearing in a term where it should.
#
# Velocity is the same, but pressure scales by 1000.
#
#

import petsc4py
import underworld3 as uw
import numpy as np


# +
import pygmsh

# Mesh a 2D pipe with a circular hole

csize = 0.025
csize_circle = 0.02
res = csize_circle

width = 2.2
height = 0.41
radius = 0.05


if uw.mpi.rank == 0:

    # Generate local mesh on boss process

    with pygmsh.geo.Geometry() as geom:

        geom.characteristic_length_max = csize

        inclusion = geom.add_circle(
            (0.2, 0.2, 0.0), radius, make_surface=False, mesh_size=csize_circle
        )
        domain = geom.add_rectangle(
            xmin=0.0,
            ymin=0.0,
            xmax=width,
            ymax=height,
            z=0,
            holes=[inclusion],
            mesh_size=csize,
        )

        geom.add_physical(domain.surface.curve_loop.curves[0], label="bottom")
        geom.add_physical(domain.surface.curve_loop.curves[1], label="right")
        geom.add_physical(domain.surface.curve_loop.curves[2], label="top")
        geom.add_physical(domain.surface.curve_loop.curves[3], label="left")

        geom.add_physical(inclusion.curve_loop.curves, label="inclusion")

        geom.add_physical(domain.surface, label="Elements")

        geom.generate_mesh(dim=2, verbose=False)
        geom.save_geometry("ns_pipe_flow.msh")
        geom.save_geometry("ns_pipe_flow.vtk")

# -


pipemesh = uw.discretisation.Mesh(degree=1, meshfile="ns_pipe_flow.msh")
pipemesh.dm.view()

# +
# check the mesh if in a notebook / serial


if uw.mpi.size == 1:
    import numpy as np
    import pyvista as pv
    import vtk

    pv.global_theme.background = "white"
    pv.global_theme.window_size = [1050, 500]
    pv.global_theme.antialiasing = True
    pv.global_theme.jupyter_backend = "panel"
    pv.global_theme.smooth_shading = True
    pv.global_theme.camera["viewup"] = [0.0, 1.0, 0.0]
    pv.global_theme.camera["position"] = [0.0, 0.0, 1.0]

    pipemesh.vtk("mesh_tmp.vtk")
    pvmesh = pv.read("mesh_tmp.vtk")

    pl = pv.Plotter()

    points = np.zeros((pipemesh._centroids.shape[0], 3))
    points[:, 0] = pipemesh._centroids[:, 0]
    points[:, 1] = pipemesh._centroids[:, 1]

    point_cloud = pv.PolyData(points)

    # pl.add_mesh(pvmesh,'Black', 'wireframe', opacity=0.5)
    pl.add_mesh(
        pvmesh,
        cmap="coolwarm",
        edge_color="Black",
        show_edges=True,
        use_transparency=False,
        opacity=0.5,
    )

    pl.add_points(
        point_cloud,
        color="Blue",
        render_points_as_spheres=True,
        point_size=2,
        opacity=1.0,
    )

    pl.show(cpos="xy")

# +
# Define some functions on the mesh

import sympy

# radius_fn = sympy.sqrt(pipemesh.rvec.dot(pipemesh.rvec)) # normalise by outer radius if not 1.0
# unit_rvec = pipemesh.rvec / (1.0e-10+radius_fn)

# Some useful coordinate stuff

x = pipemesh.N.x
y = pipemesh.N.y

# relative to the centre of the inclusion
r = sympy.sqrt((x - 1.0) ** 2 + (y - 0.5) ** 2)
th = sympy.atan2(y - 0.5, x - 1.0)

# need a unit_r_vec equivalent

inclusion_rvec = pipemesh.rvec - 1.0 * pipemesh.N.i - 0.5 * pipemesh.N.j
inclusion_unit_rvec = inclusion_rvec / inclusion_rvec.dot(inclusion_rvec)

# -

v_soln = uw.discretisation.MeshVariable("U", pipemesh, pipemesh.dim, degree=2)
vs_soln = uw.discretisation.MeshVariable("Us", pipemesh, pipemesh.dim, degree=2)
v_stokes = uw.discretisation.MeshVariable("U_0", pipemesh, pipemesh.dim, degree=2)
p_soln = uw.discretisation.MeshVariable("P", pipemesh, 1, degree=1)
vorticity = uw.discretisation.MeshVariable("omega", pipemesh, 1, degree=1)
r_inc = uw.discretisation.MeshVariable("R", pipemesh, 1, degree=1)


# +
swarm = uw.swarm.Swarm(mesh=pipemesh)
v_star = uw.swarm.SwarmVariable("Vs", swarm, pipemesh.dim, proxy_degree=4)
remeshed = uw.swarm.SwarmVariable("Vw", swarm, 1, dtype="int", _proxy=False)
X_0 = uw.swarm.SwarmVariable("X0", swarm, pipemesh.dim, _proxy=False)

swarm.populate(fill_param=5)

# +
passive_swarm = uw.swarm.Swarm(mesh=pipemesh)
passive_swarm.populate(
    fill_param=1,
)

with passive_swarm.access(passive_swarm.particle_coordinates):
    passive_swarm.particle_coordinates.data[:, 0] /= 2.0 * width
    passive_swarm.particle_coordinates.data[:, 1] = 0.2


# +
# Create NS object

navier_stokes = uw.systems.NavierStokesSwarm(
    pipemesh,
    velocityField=v_soln,
    pressureField=p_soln,
    velocityStar_fn=v_star.fn,
    u_degree=v_soln.degree,
    p_degree=p_soln.degree,
    rho=1.0,
    theta=0.5,
    verbose=False,
    projection=True,
    solver_name="navier_stokes",
)


navier_stokes.petsc_options.delValue(
    "ksp_monitor"
)  # We can flip the default behaviour at some point
navier_stokes._u_star_projector.petsc_options.delValue("ksp_monitor")
navier_stokes._u_star_projector.petsc_options["snes_rtol"] = 3.0e-2
navier_stokes._u_star_projector.smoothing = 0.0  # navier_stokes.viscosity * 1.0e-6
# -


navier_stokes.petsc_options.delValue(
    "ksp_monitor"
)  # We can flip the default behaviour at some point
navier_stokes._u_star_projector.petsc_options.delValue("ksp_monitor")

nodal_vorticity_from_v = uw.systems.Projection(pipemesh, vorticity)
nodal_vorticity_from_v.uw_function = sympy.vector.curl(v_soln.fn).dot(pipemesh.N.k)
nodal_vorticity_from_v.smoothing = 1.0e-3
nodal_vorticity_from_v.petsc_options.delValue("ksp_monitor")

# +
# nodal_v_star_from_v = uw.systems.Vector_Projection(pipemesh, vs_soln, verbose=True)
# nodal_v_star_from_v.uw_function = v_star.fn
# nodal_v_star_from_v.smoothing = 1.0e-6


# +
# Set solve options here (or remove default values
# stokes.petsc_options.getAll()

# Constant visc

navier_stokes.rho = 1000
navier_stokes.theta = 0.5
navier_stokes.penalty = 0.0
navier_stokes.viscosity = 1.0
navier_stokes.bodyforce = 1.0e-32 * pipemesh.N.i
navier_stokes._Ppre_fn = 1.0 / (
    navier_stokes.viscosity + navier_stokes.rho / navier_stokes.delta_t
)

U0 = 1.5
Vb = (4.0 * U0 * y * (0.41 - y)) / 0.41**2

expt_name = "NS_benchmark_DFG2d_2iii"

hw = 1000.0 / res
with pipemesh.access(r_inc):
    r_inc.data[:, 0] = uw.function.evaluate(r, pipemesh.data)

surface_defn = sympy.exp(-(((r_inc.fn - radius) / radius) ** 2) * hw)

# Velocity boundary conditions

navier_stokes.add_dirichlet_bc((0.0, 0.0), "inclusion", (0, 1))
navier_stokes.add_dirichlet_bc((0.0, 0.0), "top", (0, 1))
navier_stokes.add_dirichlet_bc((0.0, 0.0), "bottom", (0, 1))
navier_stokes.add_dirichlet_bc((Vb, 0.0), "left", (0, 1))


# +
## Initial rough guess - 3.5 s is the recommended ramp up time and is
## related to how long it takes to establish a flow in the entire pipe
## We get that started then move to short steps to look at unsteady
## flow though that will probably take another 3.5 seconds to work !

navier_stokes.solve(timestep=3.5)  # Stokes-like initial flow

# +
with pipemesh.access(v_stokes, v_soln):
    v_stokes.data[...] = v_soln.data[...]
    v_soln.data[...] += U0 / 100 * np.random.random(size=v_soln.data.shape)
    v_soln.data[...] *= 1.0 + 0.1 * np.cos(
        v_soln.coords[:, 1].reshape(-1, 1) / 0.41 * np.pi
    )

with swarm.access(v_star, remeshed, X_0):
    v_star.data[...] = uw.function.evaluate(v_soln.fn, swarm.data)
    X_0.data[...] = swarm.data[...]
    remeshed.data[...] = 0
# -


swarm.advection(v_soln.fn, delta_t=navier_stokes.estimate_dt(), corrector=False)

# +
# check the mesh if in a notebook / serial


if uw.mpi.size == 1:

    import numpy as np
    import pyvista as pv
    import vtk

    pv.global_theme.background = "white"
    pv.global_theme.window_size = [1250, 1250]
    pv.global_theme.antialiasing = True
    pv.global_theme.jupyter_backend = "panel"
    pv.global_theme.smooth_shading = True
    # pv.global_theme.camera['viewup'] = [0.0, 1.0, 0.0]
    # pv.global_theme.camera['position'] = [0.0, 0.0, 1.0]

    pvmesh = pipemesh.mesh2pyvista(elementType=vtk.VTK_TRIANGLE)

    #     points = np.zeros((t_soln.coords.shape[0],3))
    #     points[:,0] = t_soln.coords[:,0]
    #     points[:,1] = t_soln.coords[:,1]

    #     point_cloud = pv.PolyData(points)

    with pipemesh.access():
        usol = navier_stokes._u_star_projector.u.data.copy()
        usol = v_soln.data.copy()

    with pipemesh.access():
        pvmesh.point_data["Vmag"] = uw.function.evaluate(
            sympy.sqrt(v_soln.fn.dot(v_soln.fn)), pipemesh.data
        )
        pvmesh.point_data["P"] = uw.function.evaluate(p_soln.fn, pipemesh.data)
        pvmesh.point_data["dVy"] = uw.function.evaluate(
            (v_soln.fn - v_stokes.fn).dot(pipemesh.N.j), pipemesh.data
        )

    v_vectors = np.zeros((pipemesh.data.shape[0], 3))
    v_vectors[:, 0:2] = uw.function.evaluate(v_soln.fn, pipemesh.data)
    pvmesh.point_data["V"] = v_vectors

    arrow_loc = np.zeros((v_soln.coords.shape[0], 3))
    arrow_loc[:, 0:2] = v_soln.coords[...]

    arrow_length = np.zeros((v_soln.coords.shape[0], 3))
    arrow_length[:, 0:2] = usol[...]

    # point sources at cell centres

    points = np.zeros((pipemesh._centroids.shape[0], 3))
    points[:, 0] = pipemesh._centroids[:, 0]
    points[:, 1] = pipemesh._centroids[:, 1]
    point_cloud = pv.PolyData(points)

    pvstream = pvmesh.streamlines_from_source(
        point_cloud, vectors="V", integration_direction="both", max_steps=100
    )

    pl = pv.Plotter()

    pl.add_arrows(arrow_loc, arrow_length, mag=0.05 / U0, opacity=0.75)

    # pl.add_points(point_cloud, cmap="coolwarm",
    #               render_points_as_spheres=False,
    #               point_size=10, opacity=0.66
    #             )

    pl.add_mesh(
        pvmesh,
        cmap="coolwarm",
        edge_color="Black",
        show_edges=True,
        scalars="Vmag",
        use_transparency=False,
        opacity=1.0,
    )

    # pl.add_mesh(pvmesh,'Black', 'wireframe', opacity=0.75)
    pl.add_mesh(pvstream)

    # pl.remove_scalar_bar("mag")

    pl.show()


# -
def plot_V_mesh(filename):

    if uw.mpi.size == 1:

        import numpy as np
        import pyvista as pv
        import vtk

        pv.global_theme.background = "white"
        pv.global_theme.window_size = [1250, 1000]
        pv.global_theme.antialiasing = True
        pv.global_theme.jupyter_backend = "panel"
        pv.global_theme.smooth_shading = True
        # pv.global_theme.camera['viewup'] = [0.0, 1.0, 0.0]
        # pv.global_theme.camera['position'] = [0.0, 0.0, 2.0]

        pvmesh = pipemesh.mesh2pyvista(elementType=vtk.VTK_TRIANGLE)

        with passive_swarm.access():
            points = np.zeros((passive_swarm.data.shape[0], 3))
            points[:, 0] = passive_swarm.data[:, 0]
            points[:, 1] = passive_swarm.data[:, 1]

        point_cloud = pv.PolyData(points)

        points = np.zeros((pipemesh._centroids.shape[0], 3))
        points[:, 0] = pipemesh._centroids[:, 0]
        points[:, 1] = pipemesh._centroids[:, 1]

        c_point_cloud = pv.PolyData(points)

        with pipemesh.access():
            pvmesh.point_data["P"] = uw.function.evaluate(p_soln.fn, pipemesh.data)
            pvmesh.point_data["dVy"] = uw.function.evaluate(
                (v_soln.fn - v_stokes.fn).dot(pipemesh.N.j), pipemesh.data
            )
            pvmesh.point_data["Omega"] = uw.function.evaluate(
                vorticity.fn, pipemesh.data
            )

        with pipemesh.access():
            usol = v_soln.data.copy()

        v_vectors = np.zeros((pipemesh.data.shape[0], 3))
        v_vectors[:, 0:2] = uw.function.evaluate(v_soln.fn, pipemesh.data)
        pvmesh.point_data["V"] = v_vectors

        arrow_loc = np.zeros((v_soln.coords.shape[0], 3))
        arrow_loc[:, 0:2] = v_soln.coords[...]

        arrow_length = np.zeros((v_soln.coords.shape[0], 3))
        arrow_length[:, 0:2] = usol[...]

        pl = pv.Plotter()

        pl.add_arrows(arrow_loc, arrow_length, mag=0.033 / U0, opacity=0.5)

        pvstream = pvmesh.streamlines_from_source(
            c_point_cloud, vectors="V", integration_direction="both", max_time=0.25
        )

        # pl.add_mesh(pvmesh,'Black', 'wireframe', opacity=0.75)

        pl.add_mesh(
            pvmesh,
            cmap="coolwarm",
            edge_color="Black",
            show_edges=False,
            scalars="Omega",
            use_transparency=False,
            opacity=0.5,
        )

        pl.add_mesh(pvstream)

        pl.add_points(
            point_cloud,
            color="Black",
            render_points_as_spheres=True,
            point_size=2.5,
            opacity=0.75,
        )

        pl.camera.SetPosition(0.75, 0.2, 3.0)
        pl.camera.SetFocalPoint(0.75, 0.2, 0.0)
        pl.camera.SetClippingRange(1.0, 8.0)

        pl.remove_scalar_bar("Omega")
        pl.remove_scalar_bar("mag")
        pl.remove_scalar_bar("V")

        pl.screenshot(
            filename="{}.png".format(filename),
            window_size=(2560, 1280),
            return_img=False,
        )

        pl.close()

        del pl

    # pl.show()


ts = 0
dt_ns = 1.0e-2
swarm_loop = 5

navier_stokes._u_star_projector.smoothing = 1.0e-6 * navier_stokes.viscosity
uw_fn = navier_stokes._u_star_projector.uw_function

# +
for step in range(0, 500):
    delta_t_swarm = 5.0 * navier_stokes.estimate_dt()
    delta_t = delta_t_swarm  # min(delta_t_swarm, dt_ns)

    navier_stokes.solve(timestep=dt_ns, zero_init_guess=False)

    with swarm.access(v_star):
        v_star.data[...] = uw.function.evaluate(v_soln.fn, swarm.data)

    # update passive swarm

    passive_swarm.advection(v_soln.fn, delta_t, corrector=False)

    npoints = 50
    passive_swarm.dm.addNPoints(npoints)
    with passive_swarm.access(passive_swarm.particle_coordinates):
        for i in range(npoints):
            passive_swarm.particle_coordinates.data[
                -1 : -(npoints + 1) : -1, :
            ] = np.array([0.0, 0.19] + 0.02 * np.random.random((npoints, 2)))

    # update integration swarm

    swarm.advection(v_soln.fn, delta_t, corrector=False)

    # Restore a subset of points to start
    offset_idx = step % swarm_loop

    with swarm.access(swarm.particle_coordinates, remeshed):
        remeshed.data[...] = 0
        remeshed.data[offset_idx::swarm_loop, :] = 1
        swarm.data[offset_idx::swarm_loop, :] = X_0.data[offset_idx::swarm_loop, :]

    # re-calculate v history for remeshed particles
    # Note, they may have moved procs after the access manager closed
    # so we re-index

    with swarm.access(v_star, remeshed):
        idx = np.where(remeshed.data == 1)[0]
        v_star.data[idx] = uw.function.evaluate(v_soln.fn, swarm.data[idx])

    if uw.mpi.rank == 0:
        print("Timestep {}, dt {}".format(ts, delta_t))

    if ts % 3 == 0:
        nodal_vorticity_from_v.solve()
        plot_V_mesh(filename="output/{}_step_{}".format(expt_name, ts))

        # savefile = "output/{}_ts_{}.h5".format(expt_name,step)
        # pipemesh.save(savefile)
        # v_soln.save(savefile)
        # p_soln.save(savefile)
        # vorticity.save(savefile)
        # pipemesh.generate_xdmf(savefile)

    ts += 1


# -

pipemesh.stats(v_soln.fn.dot(v_soln.fn))

# +
# check the mesh if in a notebook / serial


if uw.mpi.size == 1:

    import numpy as np
    import pyvista as pv
    import vtk

    pv.global_theme.background = "white"
    pv.global_theme.window_size = [1250, 1250]
    pv.global_theme.antialiasing = True
    pv.global_theme.jupyter_backend = "panel"
    pv.global_theme.smooth_shading = True
    # pv.global_theme.camera['viewup'] = [0.0, 1.0, 0.0]
    # pv.global_theme.camera['position'] = [0.0, 0.0, 1.0]

    pvmesh = pipemesh.mesh2pyvista(elementType=vtk.VTK_TRIANGLE)

    #     points = np.zeros((t_soln.coords.shape[0],3))
    #     points[:,0] = t_soln.coords[:,0]
    #     points[:,1] = t_soln.coords[:,1]

    #     point_cloud = pv.PolyData(points)

    with pipemesh.access():
        usol = v_soln.data.copy()

    with pipemesh.access():
        pvmesh.point_data["Vmag"] = uw.function.evaluate(
            sympy.sqrt(v_soln.fn.dot(v_soln.fn)), pipemesh.data
        )
        pvmesh.point_data["P"] = uw.function.evaluate(p_soln.fn, pipemesh.data)
        pvmesh.point_data["dVy"] = uw.function.evaluate(
            (v_soln.fn - v_stokes.fn).dot(pipemesh.N.j), pipemesh.data
        )
        pvmesh.point_data["Omega"] = uw.function.evaluate(vorticity.fn, pipemesh.data)

    v_vectors = np.zeros((pipemesh.data.shape[0], 3))
    v_vectors[:, 0:2] = uw.function.evaluate(v_soln.fn, pipemesh.data)
    pvmesh.point_data["V"] = v_vectors

    arrow_loc = np.zeros((v_soln.coords.shape[0], 3))
    arrow_loc[:, 0:2] = v_soln.coords[...]

    arrow_length = np.zeros((v_soln.coords.shape[0], 3))
    arrow_length[:, 0:2] = usol[...]

    # swarm points

    with swarm.access():
        points = np.zeros((swarm.data.shape[0], 3))
        points[:, 0] = swarm.data[:, 0]
        points[:, 1] = swarm.data[:, 1]

        swarm_point_cloud = pv.PolyData(points)

    # point sources at cell centres

    points = np.zeros((pipemesh._centroids.shape[0], 3))
    points[:, 0] = pipemesh._centroids[:, 0]
    points[:, 1] = pipemesh._centroids[:, 1]
    point_cloud = pv.PolyData(points)

    pvstream = pvmesh.streamlines_from_source(
        point_cloud,
        vectors="V",
        integration_direction="both",
        surface_streamlines=True,
        max_time=0.5,
    )

    pl = pv.Plotter()

    # pl.add_arrows(arrow_loc, arrow_length, mag=0.033/U0, opacity=0.75)

    pl.add_mesh(
        pvmesh,
        cmap="coolwarm",
        edge_color="Black",
        show_edges=False,
        scalars="Vmag",
        use_transparency=False,
        opacity=1.0,
    )

    # pl.add_points(swarm_point_cloud, color="Black",
    #               render_points_as_spheres=True,
    #               point_size=0.5, opacity=0.66
    #             )

    # pl.add_mesh(pvmesh,'Black', 'wireframe', opacity=0.75)
    # pl.add_mesh(pvstream)

    # pl.remove_scalar_bar("S")
    # pl.remove_scalar_bar("mag")

    pl.show()
# +
## Pressure difference at front/rear of the cylinder

p = uw.function.evaluate(p_soln.fn, np.array([(0.15, 0.2), (0.25, 0.2)]))

p[0] - p[1]
# -
