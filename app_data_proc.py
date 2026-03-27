import numpy as np

fields_names_dict = {
    'Demagnetization field': 'dipole',
    'Exchange field': 'exchange',
    'External field': 'zeeman',
    'Anisotropy field': 'uniaxial_anisotropy',
    'Total field': 'total',
}

def process_mode_profile_mesh(mode, component_index):
    """Return raw triangular mesh data for frontend rendering."""
    if 'Re(m)' not in mode.point_data:
        return {"error": "VTK file missing point_data 'Re(m)'"}

    values = np.asarray(mode.point_data['Re(m)'][:, component_index], dtype=float) * 1e3

    points = mode.points[:, :2]  # Nx2, drop z
    x_range = points[:, 0].max() - points[:, 0].min()

    triangles = _find_triangles(mode)
    if triangles is not None and x_range > 1e-6:
        return {
            'points': points.tolist(),
            'triangles': triangles.tolist(),
            'values': values.tolist(),
        }

    # 1D-like mesh (Plane Film): synthesize a strip with triangles
    return _synthesize_strip(mode.points, values)

def process_field_profile_mesh(field, component_index):
    values = np.asarray(field.point_data['vector'][:, component_index], dtype=float) * 1e3
    
    points = field.points[:, :2]  # Nx2, drop z
    x_range = points[:, 0].max() - points[:, 0].min()
    
    triangles = _find_triangles(field)
    if triangles is not None and x_range > 1e-6:
        return {
            'points': points.tolist(),
            'triangles': triangles.tolist(),
            'values': values.tolist(),
        }
    return _synthesize_strip(field.points, values)

def find_closest_mode(all_modes, k_value):
    k_all = [float(mode.split('k')[1].split('radperm')[0]) for mode in all_modes]
    k_all = np.sort(np.unique(np.array(k_all)))
    closest_k = k_all[np.argmin(np.abs(k_all - k_value))]
    return closest_k

def _synthesize_strip(points_3d, values):
    """Create a synthetic 2D triangle strip from a 1D chain of points."""
    y = points_3d[:, 1]
    sorted_idx = np.argsort(y)
    y_sorted = y[sorted_idx]
    values_sorted = values[sorted_idx]

    n = len(y_sorted)
    strip_points = []
    strip_values = []
    for i in range(n):
        strip_points.append([0.0, float(y_sorted[i])])
        strip_points.append([1.0, float(y_sorted[i])])
        strip_values.append(float(values_sorted[i]))
        strip_values.append(float(values_sorted[i]))

    strip_triangles = []
    for i in range(n - 1):
        bl = i * 2
        br = i * 2 + 1
        tl = (i + 1) * 2
        tr = (i + 1) * 2 + 1
        strip_triangles.append([bl, br, tl])
        strip_triangles.append([br, tr, tl])

    return {
        'points': strip_points,
        'triangles': strip_triangles,
        'values': strip_values,
    }

def _find_triangles(mode):
    triangles = None
    for cell_block in mode.cells:
        if cell_block.type == 'triangle':
            triangles = cell_block.data
            break
    if triangles is None:
        try:
            triangles = mode.get_cells_type('triangle')
        except Exception:
            pass
    return triangles
