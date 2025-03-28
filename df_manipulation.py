import numpy as np

def group_velocity(dispersion):
    dk = np.diff(dispersion['k (rad/m)'])
    shifted_k = dispersion['k (rad/m)'] + abs(dispersion['k (rad/m)'][0] - dispersion['k (rad/m)'][1])/2
    dispersion['kshift (rad/m)'] = np.insert(shifted_k[:-1], len(shifted_k[:-1])//2, 0)
    
    for freq_name in dispersion.keys():
        if 'Hz' in freq_name and 'Gamma' not in freq_name:
            freq = dispersion[freq_name]
            dw = np.diff(freq) * 2 * np.pi * 1e9
            velocity = dw/dk
            dispersion[f"v{freq_name[1]} (m/s)"] = np.insert(velocity, len(velocity)//2, 0)
                 
    return dispersion

def lifetime(dispersion):
    for gamma_name in dispersion.keys():
        if 'Gamma' in gamma_name:
            gamma = dispersion[gamma_name]
            lifetime = 1/gamma
            dispersion[f"lt{gamma_name[5]} (ns)"] = lifetime * 1e9 / 2 / np.pi
            dispersion.drop(columns=[gamma_name], inplace=True)
            
    return dispersion

def propagation_length(dispersion):
    for col_name in dispersion.keys():
        if 'm/s' in col_name:
            velocity = dispersion[col_name]
            lifetime = dispersion[f"lt{col_name[1]} (ns)"]
            dispersion[f"pl{col_name[1]} (µm)"] = velocity * lifetime / 1e3
            
    return dispersion