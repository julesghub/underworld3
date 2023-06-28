# ---
# jupyter:
#   jupytext:
#     formats: py:light
#     text_representation:
#       extension: .py
#       format_name: light
#       format_version: '1.5'
#       jupytext_version: 1.14.4
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# ## Viscous fingering model
#
# Based on Darcy flow and advection-diffusion of two fluids with varying viscosity.
#
# From [Guy Simpson - Practical Finite Element Modeling in Earth Science using Matlab (2017)](https://www.wiley.com/en-au/Practical+Finite+Element+Modeling+in+Earth+Science+using+Matlab-p-9781119248620)
#
# - Section 10.2 of the book
#
# #### Darcy pressure solution (quasi-static)
#
# $$\nabla \cdot \left( \boldsymbol\kappa \nabla p - \boldsymbol{s} \right) + W = 0$$
#
# #### Darcy velocity:
# $$u = - \frac{k}{\mu_c}\nabla p$$
#
# $$\nabla \cdot \mathbf{u} = 0$$
#
#
# ### viscosity:
# $$\mu_c = \left( \frac{c}{\mu_o^{\frac{1}{4}}} +  \frac{1-c}{\mu_s^{\frac{1}{4}}} \right)^{-4}$$
#
# #### Advection-diffusion of material type (solvent / oil):
#
# $$\varphi \frac{\partial c}{\partial t} + \mathbf{u} \cdot \nabla c = \nabla(\kappa\nabla c)$$
#
#
# ##### Model physical parameters:
#
# | parameter | symbol  | value  | units  |   |
# |---|---|---|---|---|
# | x |  | $$10$$  | $$m$$  |   |
# | y  |  | $$10$$  | $$m$$  |   |
# | permeability  | $$k$$ | $$10^{-13}$$  | $$m^2$$  |   |
# | porosity  | $$\varphi$$ | $$0.1$$ |   |   |
# | diffusivity  | $$\kappa$$  | $$10^{-9}$$  | $$m^2 s^{-1}$$  |   |
# | viscosity (solvent)  | $$\eta{_s}$$ | $$1.33{\cdot}10^{-4}$$  | $$Pa s$$  |   |
# | viscosity (oil)  | $$\eta{_o}$$ | $$20\eta_s$$  | $$Pa s$$  |   |
# | pressure  | $$p$$  | $$10^{5}$$  | $$Pa$$  |   |
#

# +
from petsc4py import PETSc
import underworld3 as uw
import numpy as np
import sympy

from scipy.interpolate import griddata, interp1d

import matplotlib.pyplot as plt

import os


# + language="sh"
#
# ls -trl /Users/lmoresi/+Simulations/PorousFlow/viscousFingering_example_8/* | tail -10

# +
## Reading the checkpoints back in ... 

step = 95

checkpoint_dir = "/Users/lmoresi/+Simulations/PorousFlow/viscousFingering_example_7"
checkpoint_base = "simpson_ViscousFinger"
base_filename = os.path.join(checkpoint_dir, checkpoint_base)

# +
mesh = uw.discretisation.Mesh(f"{base_filename}.mesh.00000.h5")

x,y = mesh.X

minX = mesh.data[:,0].min()
minY = mesh.data[:,1].min()
maxX = mesh.data[:,0].max()
maxY = mesh.data[:,1].max()

v_soln_ckpt = uw.discretisation.MeshVariable("U", mesh, mesh.dim, degree=1)
p_soln_ckpt = uw.discretisation.MeshVariable("P", mesh, 1, degree=2)
mat_ckpt = uw.discretisation.MeshVariable("omega", mesh, 1, degree=3)

vizmesh = uw.meshing.UnstructuredSimplexBox(
    minCoords=(minX, minY), maxCoords=(maxX, maxY), cellSize=maxY/300, qdegree=1)


v_soln_ckpt.read_timestep(checkpoint_base, "U", step, outputPath=checkpoint_dir)
p_soln_ckpt.read_timestep(checkpoint_base, "P", step, outputPath=checkpoint_dir)
mat_ckpt.read_timestep(checkpoint_base, "mat", step, outputPath=checkpoint_dir)


# +
if uw.mpi.size == 1:
    
    # plot the mesh
    import numpy as np
    import pyvista as pv
    import vtk

    pv.global_theme.background = "white"
    pv.global_theme.window_size = [750, 750]
    pv.global_theme.anti_aliasing = 'ssaa'
    pv.global_theme.jupyter_backend = "panel"
    pv.global_theme.smooth_shading = True

    vizmesh.vtk("viz_mesh.vtk")
    pvmesh = pv.read("viz_mesh.vtk")
    
    v_vectors = np.zeros((vizmesh.data.shape[0], 3))
    v_vectors[:, 0] = uw.function.evalf(v_soln_ckpt[0].sym, vizmesh.data)
    v_vectors[:, 1] = uw.function.evalf(v_soln_ckpt[1].sym, vizmesh.data)
    pvmesh.point_data["V"] = v_vectors

    arrow_loc = np.zeros((v_soln_ckpt.coords.shape[0], 3))
    arrow_loc[:, 0:2] = v_soln_ckpt.coords[...]

    with mesh.access():
        arrow_length = np.zeros((v_soln_ckpt.coords.shape[0], 3))
        arrow_length[:, 0:2] = v_soln_ckpt.data[...]
        arrow_length /= arrow_length.max()
    
    pvmesh['mat'] =  uw.function.evalf(mat_ckpt.sym[0], vizmesh.data) #uw.function.evaluate(mat.sym[0], mesh.data)
    pvmesh['p'] =  uw.function.evalf(p_soln_ckpt.sym[0], vizmesh.data) #uw.function.evaluate(mat.sym[0], mesh.data)
    
    pl = pv.Plotter()

    pl.add_mesh(pvmesh, style="wireframe", cmap="RdYlBu_r", edge_color="Grey", scalars="mat",
                show_edges=True, line_width=0.05, use_transparency=False, opacity=1)
    
#     pl.add_mesh(pvmesh, cmap="RdYlBu_r", edge_color="Grey", scalars="mat",
#                 show_edges=False, use_transparency=False, opacity=0.5)
  


    pl.add_arrows(arrow_loc, arrow_length, mag=250, opacity=1)


    pl.show(cpos="xy")
# -

I = uw.maths.Integral(mesh, sympy.sqrt(v_soln_ckpt.sym.dot(v_soln_ckpt.sym)))
Vrms = I.evaluate() 
I.fn = 1.0
Vrms /= I.evaluate()
Vrms
