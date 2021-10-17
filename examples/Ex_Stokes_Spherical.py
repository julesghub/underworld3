# %%
from petsc4py import PETSc
import underworld3 as uw
from underworld3.systems import Stokes
import numpy as np
options = PETSc.Options()
# options["help"] = None
# options["pc_type"]  = "svd"
options["ksp_rtol"] =  1.0e-5
options["ksp_monitor_short"] = None
# options["snes_type"]  = "fas"
options["snes_converged_reason"] = None
options["snes_monitor_short"] = None
# options["snes_view"]=None
# options["snes_test_jacobian"] = None
options["snes_max_it"] = 1
options["pc_type"] = "fieldsplit"
options["pc_fieldsplit_type"] = "schur"
options["pc_fieldsplit_schur_factorization_type"] ="full"
options["pc_fieldsplit_schur_precondition"] = "a11"
options["fieldsplit_velocity_pc_type"] = "lu"
options["fieldsplit_pressure_ksp_rtol"] = 1.e-5
options["fieldsplit_pressure_pc_type"] = "lu"

# %%
# some things
cell_size = 0.02
r_i       = 0.5
r_o       = 1.0
mesh = uw.mesh.SphericalShell(dim=2,
                              radius_inner=r_i,
                              radius_outer=r_o,
                              cell_size=cell_size)

# %%
# Create Stokes object
stokes = Stokes(mesh,u_degree=2,p_degree=1)
# Constant visc
stokes.viscosity = 1.
# No slip boundary conditions
stokes.add_dirichlet_bc( (0.,0.), mesh.boundary.ALL_BOUNDARIES, (0,1) )

# %%
# Set more some things
dens_ball = 10.
dens_other = 1.
position_ball = 0.75*mesh.N.j
radius_ball = 0.2

# %%
# Create a density profile
import sympy
off_rvec = mesh.rvec - position_ball
abs_r = off_rvec.dot(off_rvec)
density = sympy.Piecewise( ( dens_ball,    abs_r < radius_ball**2 ),
                           ( dens_other,                   True ) )
density

# %%
# Write density into a variable for saving
densvar = uw.mesh.MeshVariable("density",mesh,1)
with mesh.access(densvar):
    densvar.data[:,0] = uw.function.evaluate(density,densvar.coords)

# %%
# body force
unit_rvec = mesh.rvec / sympy.sqrt(mesh.rvec.dot(mesh.rvec))
stokes.bodyforce = -unit_rvec*density
stokes.bodyforce

# %%
# Solve time
stokes.solve()

# %%
import os
os.makedirs("output",exist_ok=True)
savefile = "output/stokes_spherical_2d.h5" 
mesh.save(savefile)
stokes.u.save(savefile)
stokes.p.save(savefile)
densvar.save(savefile)
mesh.generate_xdmf(savefile)

# %%
import k3d
import plot
umag = stokes.u.fn.dot(stokes.u.fn)
vertices_2d = plot.mesh_coords(mesh)
vertices = np.zeros((vertices_2d.shape[0],3),dtype=np.float32)
vertices[:,0:2] = vertices_2d[:]
indices = plot.mesh_faces(mesh)
kplot = k3d.plot()
with mesh.access():
    kplot += k3d.mesh(vertices, indices, attribute=uw.function.evaluate(umag,stokes.u.coords),wireframe=False)
kplot.grid_visible=False
kplot.display()
kplot.camera = [-0.2, 0.2, 2.0,0.,0.,0.,-0.5,1.0,-0.1]  # these are some adhoc settings

# %%
# now do 3D
cell_size=0.035
mesh = uw.mesh.SphericalShell(dim=3,radius_inner=r_i, radius_outer=r_o,cell_size=cell_size)

# %%
# Create Stokes object
stokes = Stokes(mesh,u_degree=2,p_degree=1)
# Constant visc
stokes.viscosity = 1.
# No slip boundary conditions
stokes.add_dirichlet_bc( (0.,0.,0.), mesh.boundary.ALL_BOUNDARIES, (0,1,2) )

# %%
# Create a density profile
import sympy
off_rvec = mesh.rvec - position_ball
abs_r = off_rvec.dot(off_rvec)
density = sympy.Piecewise( ( dens_ball,    abs_r < radius_ball**2 ),
                           ( dens_other,                   True ) )
density

# %%
# Write density into a variable for saving
densvar = uw.mesh.MeshVariable("density",mesh,1)
with mesh.access(densvar):
    densvar.data[:,0] = uw.function.evaluate(density,densvar.coords)

# %%
# body force
unit_rvec = mesh.rvec / sympy.sqrt(mesh.rvec.dot(mesh.rvec))
stokes.bodyforce = -unit_rvec*density
stokes.bodyforce

# %%
stokes.solve()

# %%
savefile = "output/stokes_spherical_3d.h5" 
mesh.save(savefile)
stokes.u.save(savefile)
stokes.p.save(savefile)
densvar.save(savefile)
mesh.generate_xdmf(savefile)

# %%
import k3d
import plot
umag = stokes.u.fn.dot(stokes.u.fn)
vertices = np.array(plot.mesh_coords(mesh),dtype=np.float32)
indices = plot.mesh_faces(mesh)
kplot = k3d.plot()
with mesh.access():
    kplot += k3d.mesh(vertices, indices, attribute=uw.function.evaluate(umag,stokes.u.coords),wireframe=True)
kplot.grid_visible=False
kplot.display()
kplot.camera = [-0.2, 0.2, 2.0,0.,0.,0.,-0.5,1.0,-0.1]  # these are some adhoc settings

# %%
