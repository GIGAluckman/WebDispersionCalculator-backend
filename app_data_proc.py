import numpy as np
from scipy.interpolate import griddata

def process_mode_profile_waveguide(mode, component_index, db_data):
    """Process mode profile for waveguide geometry."""
    width = float(db_data.get('width'))
    thickness = float(db_data.get('thickness'))
    cell_size_width = int(db_data.get('dWidth', 5))
    cell_size_thickness = int(db_data.get('dThick', 5))

    if 'Re(m)' not in mode.point_data:
        return {"error": "VTK file missing point_data 'Re(m)'"}

    triangles = _find_triangles(mode)
    if triangles is None:
        return {"error": "VTK file has no triangle cells"}

    points = mode.points
    re_m = mode.point_data['Re(m)']
    values = np.asarray(re_m[:, component_index], dtype=float) * 1e3

    xy = points[:, :2]

    x_min, x_max = xy[:, 0].min(), xy[:, 0].max()
    y_min, y_max = xy[:, 1].min(), xy[:, 1].max()

    xi = np.linspace(x_min, x_max, int(width/cell_size_width))
    yi = np.linspace(y_min, y_max, int(thickness/cell_size_thickness))
    Xi, Yi = np.meshgrid(xi, yi)
    Zi = griddata(xy, values, (Xi, Yi), method='cubic', fill_value=np.nan)
    Zi = np.where(np.isnan(Zi), 0, Zi)
    
    response_data = {
        'x': xi.tolist(),
        'y': yi.tolist(),
        'z': Zi.tolist(),
    }
    print("Mode profile processed successfully with grid size:", len(xi), "x", len(yi))
    return response_data

def process_mode_profile_plane_film(mode, component_index):
    """Process mode profile for plane film geometry."""
    if 'Re(m)' not in mode.point_data:
        return {"error": "VTK file missing point_data 'Re(m)'"}

    triangles = _find_triangles(mode)
    if triangles is None:
        return {"error": "VTK file has no triangle cells"}

    points = mode.points
    re_m = mode.point_data['Re(m)']
    values = np.asarray(re_m[:, component_index], dtype=float) * 1e3

    y = points[:, 1]
    sorted_idx = np.argsort(y)
    y_sorted = y[sorted_idx].tolist()
    values_sorted = values[sorted_idx]

    response_data = {
        'x': [0.0],
        'y': y_sorted,
        'z': [[v] for v in values_sorted],
    }
    print("Mode profile processed successfully with grid size: 1 x", len(y_sorted))
    return response_data

def process_mode_profile_wire(mode, component_index, db_data):
    radius = float(db_data.get('radius'))
    cell_size = int(db_data.get('dRadius', 5))
    """Process mode profile for wire geometry."""
    if 'Re(m)' not in mode.point_data:
        return {"error": "VTK file missing point_data 'Re(m)'"}

    triangles = _find_triangles(mode)
    if triangles is None:
        return {"error": "VTK file has no triangle cells"}

    points = mode.points
    re_m = mode.point_data['Re(m)']
    values = np.asarray(re_m[:, component_index], dtype=float) * 1e3

    xy = points[:, :2]
    r_min, r_max = xy[:, 0].min(), xy[:, 0].max()
    ri = np.linspace(r_min, r_max, int(radius/cell_size))
    Xi, Yi = np.meshgrid(ri, ri)
    Zi = griddata(xy, values, (Xi, Yi), method='cubic', fill_value=np.nan)
    Zi = np.where(np.isnan(Zi), 0, Zi)

    response_data = {
        'x': ri.tolist(),
        'y': ri.tolist(),
        'z': Zi.tolist(),
    }
    print("Mode profile processed successfully with grid size:", len(ri), "x", len(ri))
    return response_data

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