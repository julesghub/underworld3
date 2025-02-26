import underworld3
from underworld3.coordinates import CoordinateSystem, CoordinateSystemType
import underworld3.timing as timing
import sympy


class mesh_vector_calculus:
    """Vector calculus on uw row matrices
    - this class is designed to augment the functionality of a mesh"""

    def __init__(self, mesh):
        self.mesh = mesh
        self.dim = self.mesh.dim

    def curl(self, matrix):
        r"""
        \( \nabla \times \mathbf{v} \)

        Returns the curl of a 3D vector field or the out-of-plane
        component of a 2D vector field
        """

        vector = self.to_vector(matrix)
        vector_curl = sympy.vector.curl(vector)

        if self.dim == 3:
            return self.to_matrix(vector_curl)
        else:
            # if 2d, the out-of-plane vector is not defined in the basis so a scalar is returned (cf. vorticity)
            return vector_curl.dot(self.mesh.N.k)

    def divergence(self, matrix):
        r"""
        \( \nabla \cdot \mathbf{v} \)
        """
        vector = self.to_vector(matrix)
        scalar_div = sympy.vector.divergence(vector)
        return scalar_div

    def gradient(self, scalar):
        r"""
        $\nabla \phi$
        """

        if isinstance(scalar, sympy.Matrix) and scalar.shape == (1, 1):
            scalar = scalar[0, 0]

        vector_gradient = sympy.vector.gradient(scalar)
        return self.to_matrix(vector_gradient)

    def strain_tensor(self, vector):
        """
        Components of the infinitessimal strain or strain-rate tensor where the
        vector that is provided is displacement or velocity respectively
        """

        # Coerce vector to sympy.Matrix form
        matrix = self.to_matrix(vector)

        L = matrix.jacobian(self.mesh.CoordinateSystem.N)
        E = (L + L.transpose()) / 2

        return E

    def to_vector(self, matrix):

        if isinstance(matrix, sympy.vector.Vector):
            return matrix  # No need to convert

        if matrix.shape == (1, self.dim):
            vector = sympy.vector.matrix_to_vector(matrix, self.mesh.N)
        elif matrix.shape == (self.dim, 1):
            vector = sympy.vector.matrix_to_vector(matrix.T, self.mesh.N)
        elif matrix.shape == (1, 1):
            vector = matrix[0, 0]
        else:
            print(f"Unable to convert matrix of size {matrix.shape} to sympy.vector")
            vector = None

        return vector

    def to_matrix(self, vector):

        # Almost there ...

        if isinstance(vector, sympy.Matrix) and vector.shape == (1, self.dim):
            return vector

        if isinstance(vector, sympy.Matrix) and vector.shape == (self.dim, 1):
            return vector.T

        if isinstance(vector, sympy.Matrix) and vector.shape == (1, 1):
            return vector

        matrix = sympy.Matrix.zeros(1, self.dim)
        base_vectors = self.mesh.N.base_vectors()

        for i in range(self.dim):
            matrix[0, i] = vector.dot(base_vectors[i])

        return matrix

    def jacobian(self, matrix):
        """Jacobian of a quantity (scalar, vector, matrix) wrt the natural mesh coordinates"""

        if isinstance(matrix, sympy.vector.Vector):
            matrix_form = self.to_matrix(matrix)
        else:
            matrix_form = matrix

        jac = matrix_form.jacobian(self.mesh.CoordinateSystem.N)

        return jac


class mesh_vector_calculus_cylindrical(mesh_vector_calculus):
    """
    mesh_vector_calculus module for div, grad, curl etc that apply in
    native cylindrical coordinates
    """

    def __init__(self, mesh):

        coordinate_type = mesh.CoordinateSystem.coordinate_type

        # validation

        if not (
            coordinate_type == CoordinateSystemType.CYLINDRICAL2D_NATIVE
            or coordinate_type == CoordinateSystemType.CYLINDRICAL3D_NATIVE
        ):
            print(
                f"Warning mesh type {mesh.CoordinateSystem.type} uses Cartesian coordinates not cylindrical"
            )

        super().__init__(mesh)

    def divergence(self, matrix):
        r"""
        \( \nabla \cdot \mathbf{v} \)
        """

        r = self.mesh.CoordinateSystem.N[0]
        t = self.mesh.CoordinateSystem.N[1]

        V_r = matrix[0]
        V_t = matrix[1]

        div_V = V_r.diff(r) + V_r / r + V_t.diff(t) / r

        if self.mesh.dim == 3:  # Or is this cdim ?
            z = self.mesh.CoordinateSystem.N[2]
            V_z = matrix[2]
            div_V += V_z.diff(z)

        return div_V

    def gradient(self, scalar):
        r"""
        $\nabla \phi$
        """

        if isinstance(scalar, sympy.Matrix) and scalar.shape == (1, 1):
            scalar = scalar[0, 0]

        grad_S = sympy.Matrix.zeros(1, self.mesh.dim)

        r = self.mesh.CoordinateSystem.N[0]
        t = self.mesh.CoordinateSystem.N[1]

        grad_S[0] = scalar.diff(r)
        grad_S[1] = scalar.diff(t) / r

        if self.mesh.dim == 3:  # Or is this cdim ?
            z = self.mesh.CoordinateSystem.N[2]

            grad_S[2] = scalar.diff(z)

        return grad_S

    def curl(self, matrix):
        r"""
        $\nabla \phi$
        """

        r = self.mesh.CoordinateSystem.N[0]
        t = self.mesh.CoordinateSystem.N[1]

        matrix0 = self.to_matrix(matrix)
        V_r = matrix0[0]
        V_t = matrix0[1]

        # if 2D, return a scalar of the out-of-plane curl

        if self.mesh.dim == 2:
            curl_V = V_t / r + V_t.diff(r) - V_r.diff(t) / r

        else:
            z = self.mesh.CoordinateSystem.N[2]
            V_z = matrix0[2]
            curl_V = sympy.Matrix.zeros(1, 3)

            curl_V[0] = V_z.diff(t) / r - V_t.diff(z)
            curl_V[1] = V_r.diff(z) - V_z.diff(r)
            curl_V[2] = V_t / r + V_t.diff(r) - V_r.diff(t) / r

        return curl_V

    def strain_tensor(self, vector):
        """
        Components of the infinitessimal strain or strain-rate tensor where the
        vector that is provided is displacement or velocity respectively.
        In cylindrical geometry, there are additional terms that include
        the location of each point
        """

        # Coerce vector to sympy.Matrix form
        matrix = self.to_matrix(vector)

        L = matrix.jacobian(self.mesh.CoordinateSystem.N)
        r = self.mesh.CoordinateSystem.N[0]

        vr = matrix[0]
        vt = matrix[1]

        E = L.copy()
        # E_00, E_22 and E_02 are unchanged from Cartesian

        # E[0,0] = L[0,0]
        E[1, 1] = L[1, 1] / r + vr / r
        E[1, 0] = E[0, 1] = (L[0, 1] / r + L[1, 0] - vt / r) / 2

        if self.dim == 3:
            E[1, 2] = E[2, 1] = (L[2, 1] / r + L[1, 2]) / 2
            E[0, 2] = E[2, 0] = (L[2, 0] + L[0, 2]) / 2

        return E


class mesh_vector_calculus_spherical_lonlat(mesh_vector_calculus):
    """
    mesh_vector_calculus module for div, grad, curl etc that apply in
    native cylindrical coordinates. NOTE - our choice of coordinates
    is slightly unusual - radius, longitude and latitude (in radians)
    for convenience when it comes to working with Earth-Science datasets
    """

    def __init__(self, mesh):

        coordinate_type = mesh.CoordinateSystem.coordinate_type

        # validation

        if not coordinate_type == CoordinateSystemType.SPHERICAL_NATIVE:
            print(
                f"Warning mesh type {mesh.CoordinateSystem.type} uses Cartesian coordinates not spherical"
            )

        super().__init__(mesh)

    def divergence(self, matrix):
        r"""
        \( \nabla \cdot \mathbf{v} \)
        """

        r = self.mesh.CoordinateSystem.N[0]
        l1 = self.mesh.CoordinateSystem.N[1]
        l2 = self.mesh.CoordinateSystem.N[2]

        V_r = matrix[0]
        V_l1 = matrix[1]
        V_l2 = matrix[2]

        div_V = (
            V_r.diff(r)
            + 2 * V_r / r
            + V_l1.diff(l1) / (r * sympy.cos(l2))
            - sympy.tan(l2) * V_l2 / r
            + V_l2.diff(l2) / r
        )

        return div_V

    def gradient(self, scalar):
        r"""
        $\nabla \phi$
        """

        if isinstance(scalar, sympy.Matrix) and scalar.shape == (1, 1):
            scalar = scalar[0, 0]

        grad_S = sympy.Matrix.zeros(1, 3)

        r = self.mesh.CoordinateSystem.N[0]
        l1 = self.mesh.CoordinateSystem.N[1]
        l2 = self.mesh.CoordinateSystem.N[2]

        grad_S[0] = scalar.diff(r)
        grad_S[1] = scalar.diff(l1) / (r * sympy.cos(l2))
        grad_S[2] = scalar.diff(l2) / r

        return grad_S

    def curl(self, matrix):
        r"""
        $\nabla \times \phi$ in spherical
        """

        r = self.mesh.CoordinateSystem.N[0]
        l1 = self.mesh.CoordinateSystem.N[1]
        l2 = self.mesh.CoordinateSystem.N[2]

        matrix0 = self.to_matrix(matrix)
        V_r = matrix[0]
        V_l1 = matrix[1]
        V_l2 = matrix[2]

        curl_V = sympy.Matrix.zeros(1, 3)

        curl_V[0] = (
            V_l1.diff(l2) / r
            - sympy.tan(l2) * V_l1 / r
            - V_l2.diff(l1) / (r * sympy.cos(l2))
        )
        curl_V[1] = V_l2.diff(r) + V_l2 / r - V_r.diff(l2) / r
        curl_V[2] = V_r.diff(l1) / (r * sympy.cos(l2)) - V_l1.diff(r) - V_l1 / r

        return curl_V

    def strain_tensor(self, vector):
        """
        Components of the infinitessimal strain or strain-rate tensor where the
        vector that is provided is displacement or velocity respectively.
        In cylindrical geometry, there are additional terms that include
        the location of each point
        """

        # Coerce vector to sympy.Matrix form
        matrix = self.to_matrix(vector)

        L = matrix.jacobian(self.mesh.CoordinateSystem.N)
        r = self.mesh.CoordinateSystem.N[0]
        l1 = self.mesh.CoordinateSystem.N[1]
        l2 = self.mesh.CoordinateSystem.N[2]

        V_r = matrix[0]
        V_l1 = matrix[1]
        V_l2 = matrix[2]

        E = L.copy()
        # E_00, E_22 and E_02 are unchanged from Cartesian

        E[0, 0] = L[0, 0]
        E[1, 1] = (L[1, 1] + V_r * sympy.cos(l2) + V_l2 * sympy.sin(l2)) / (
            r * sympy.cos(l2)
        )
        E[2, 2] = (L[2, 2] + V_r) / r

        E[1, 0] = E[0, 1] = (L[0, 1] / (r * sympy.cos(l2)) + L[1, 0] - V_l1 / r) / 2
        E[2, 0] = E[0, 2] = (L[0, 2] / r + L[2, 0] - V_l2 / r) / 2
        E[1, 2] = E[2, 1] = (
            L[1, 2] + L[2, 1] / sympy.cos(l2) - V_l1 * sympy.tan(l2)
        ) / (2 * r)

        return E
