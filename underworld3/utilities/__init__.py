# A collection of utilities for the uw3 system

from . import _api_tools

# get/import petsc_gen_xdmf from the original petsc installation
import sys
import petsc4py

conf = petsc4py.get_config()
petsc_dir = conf["PETSC_DIR"]
if not petsc_dir + "/lib/petsc/bin" in sys.path:
    sys.path.append(petsc_dir + "/lib/petsc/bin")

from .uw_petsc_gen_xdmf import *
from .uw_swarmIO import *

from ._utils import gather_data

# A couple of useful h5 tricks
def h5_scan(filename):

    h5file = h5py.File(filename)
    entities = []
    h5file.visit(entities.append)
    h5file.close()

    return entities
