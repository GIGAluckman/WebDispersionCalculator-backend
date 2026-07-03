import numpy as np
import os
import re

SIMULATION_DATA_PATH = os.getenv('SIMULATION_DATA_PATH', 'simulation_data')

def _mode_index(col_name):
    """Extract the full mode index from a column name like 'f10 (Hz)' or 'Gamma3 (Hz)'."""
    match = re.search(r'(\d+)', col_name)
    return match.group(1)

def group_velocity(dispersion):
    dk = np.diff(dispersion['k (rad/m)'])
    shifted_k = dispersion['k (rad/m)'] + abs(dispersion['k (rad/m)'][0] - dispersion['k (rad/m)'][1])/2
    # v = dw/dk is undefined at k=0, so a v=0 sample is inserted where the
    # midpoint grid crosses zero (the grid from tetrax is symmetric around 0).
    midpoints = np.asarray(shifted_k[:-1])
    zero_pos = np.searchsorted(midpoints, 0)
    dispersion['kshift (rad/m)'] = np.insert(midpoints, zero_pos, 0)

    for freq_name in dispersion.keys():
        if 'Hz' in freq_name and 'Gamma' not in freq_name:
            freq = dispersion[freq_name]
            dw = np.diff(freq) * 2 * np.pi
            velocity = dw/dk
            dispersion[f"v{_mode_index(freq_name)} (m/s)"] = np.insert(np.asarray(velocity), zero_pos, 0)

    return dispersion

def lifetime(dispersion):
    for gamma_name in dispersion.keys():
        if 'Gamma' in gamma_name:
            gamma = dispersion[gamma_name]
            lifetime = 1/gamma
            dispersion[f"lt{_mode_index(gamma_name)} (ns)"] = lifetime * 1e9 / 2 / np.pi
            dispersion.drop(columns=[gamma_name], inplace=True)

    return dispersion

def propagation_length(dispersion):
    for col_name in dispersion.keys():
        if 'm/s' in col_name:
            velocity = dispersion[col_name]
            mode = _mode_index(col_name)
            lifetime = dispersion[f"lt{mode} (ns)"]
            dispersion[f"pl{mode} (µm)"] = velocity * lifetime / 1e3

    return dispersion

def if_nan(dispersion):
    for col_name in dispersion.keys():
        if 'Hz' in col_name:
            if np.isnan(dispersion[col_name]).any():
                print(f"NaN found in {col_name}")
                return True
    return False

def dataframe_polish(dispersion, kmin, kmax, task_id):
    dispersion.drop(columns=['m'], inplace=True)
    dispersion['k (rad/m)'] = dispersion['k (rad/m)'] / 1e6
    dispersion.rename(columns={'k (rad/m)': 'k (rad/µm)'}, inplace=True)
    if 'kshift (rad/m)' in dispersion.keys():
        dispersion['kshift (rad/m)'] = dispersion['kshift (rad/m)'] / 1e6
        dispersion.rename(columns={'kshift (rad/m)': 'kshift (rad/µm)'}, inplace=True)
    dispersion = dispersion[dispersion['k (rad/µm)'] >= kmin]
    dispersion = dispersion[dispersion['k (rad/µm)'] <= kmax]
    
    for col in dispersion.columns:
        if 'Hz' in col and 'Gamma' not in col:
            dispersion[col] = dispersion[col] / 1e9
            dispersion.rename(columns={col: col.replace('Hz', 'GHz')}, inplace=True)
            
    # Atomic write: /status and /result poll for this file, so it must never
    # be visible in a half-written state.
    csv_path = os.path.join(SIMULATION_DATA_PATH, str(task_id), 'dispersion_data.csv')
    tmp_path = f'{csv_path}.tmp'
    dispersion.to_csv(tmp_path, index=False)
    os.replace(tmp_path, csv_path)
    return dispersion