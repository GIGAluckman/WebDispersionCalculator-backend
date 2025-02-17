import tetrax as tx
import os
from helpers import JSONHelper
from df_manipulation import *

class TetraxCalc:
    def __init__(self, data, id):
        
        if not os.path.exists(f'simulation_data/{id}'):
            os.mkdir(f'simulation_data/{id}')
        
        self.db_path = f'simulation_data/{id}/db.json'
        print('DB path:', self.db_path)
        self.task_id = id
        self.data = data
        self.geometry = data['chosenGeometry']
        self.data_parser()
        self.json_helper = JSONHelper(self.db_path)
        self.json_helper.create_db(data)
        
    def set_geometry(self):
        if self.geometry == 'Waveguide':
            mesh = tx.geometries.waveguide.rectangular(
                width=self.data['width'],
                thickness=self.data['thickness'],
                cell_size_width=int(self.data.get('dWidth', 5)),
                cell_size_thickness=int(self.data.get('dThick', 5)),
            )
            
        elif self.geometry == 'Plane Film':
            mesh = tx.geometries.layer.monolayer(
                thickness=self.data['thickness'],
                cell_size=int(self.data.get('dThick', 5)),
            )
            
        elif self.geometry == 'Wire':
            mesh = tx.geometries.waveguide.round_wire(
                radius=self.data['radius'],
                cell_size=int(self.data.get('dRadius', 5)),
            )
        
        self.sample = tx.Sample(mesh, name=f'simulation_data/{self.task_id}')
        
    def set_material(self):
        self.sample.material['Msat'] = float(self.data['saturationMagnetization'])
        self.sample.material['Aex'] = float(self.data['exchangeStiffness']) * 1e-12
        self.sample.material['alpha'] = float(self.data['GilbertDamping'])
        if 'anisotropyConstant' in self.data.keys():
            self.sample.material['Ku1'] = float(self.data['anisotropyConstant'])
            self.sample.material['e_u'] = self.data['anisotropyAxis']
            
        print('Material set with parameters:')
        print(f'Msat: {self.sample.material['Msat'].average} A/m')
        print(f'Aex: {self.sample.material['Aex'].average} J/m')
        print(f'Ku1: {self.sample.material['Ku1'].average} J/m^3')
        print(f'e_u: {self.sample.material['e_u'].average}')
        
    def calculate_dispersion(self):
        
        self.set_geometry()
        self.set_material()
        
        self.sample.mag = self.data['fieldAxis']
        self.sample.external_field = [i*self.data['externalField']/1e3 for i in self.data['fieldAxis']]
        
        print('External field set to:', self.sample.external_field[0], 'T')
        self.json_helper.set_parameter('status', 'Start relaxation')
        
        nr_trial = 0
        success = False
        while (not(success) and (nr_trial < 5)):
            relax = tx.experiments.relax(self.sample, tolerance=1e-13, verbose=False)
            success = relax.was_success
            nr_trial += 1
        if success:
            print('Default relaxation successful')
            self.json_helper.set_parameter('status', 'Relaxation successful')
        else:
            nr_trial = 0
            while (not(success) and (nr_trial < 5)):
                relax = tx.experiments.relax_dynamic(self.sample, tolerance=1e-13, verbose=False)
                success = relax.was_success
                nr_trial += 1
            if success:
                print('LLG relaxation successful')
                self.json_helper.set_parameter('status', 'Relaxation successful')
            else:
                print('Relaxation failed')
                self.json_helper.set_parameter('status', 'Relaxation unsuccessful!')
                
        self.json_helper.set_parameter('status', 'Dispersion calculation in progress')    
        
        dispersion = tx.experiments.eigenmodes(
            sample=self.sample,
            db_helper=self.json_helper,
            num_cpus=-1,
            num_modes=int(self.data['numberOfModes']),
            kmin=self.data['kMin'] * 1e6,
            kmax=self.data['kMax'] * 1e6, 
            num_k=int(self.data.get('numberOfK', 11)))
        
        dispersion.plot_linewidths()
        
        dispersion = dispersion.spectrum_dataframe
        
        for col in dispersion.columns:
            if 'Hz' in col and 'Gamma' not in col:
                dispersion[col] = dispersion[col] / 1e9
                dispersion.rename(columns={col: col.replace('Hz', 'GHz')}, inplace=True)
        
        dispersion = lifetime(dispersion)
        dispersion = group_velocity(dispersion)
        dispersion = propagation_length(dispersion)
        dispersion['k (rad/m)'] = dispersion['k (rad/m)'] / 1e6
        dispersion['kshift (rad/m)'] = dispersion['kshift (rad/m)'] / 1e6
        dispersion.rename(columns={'k (rad/m)': 'k (rad/µm)'}, inplace=True)
        dispersion.rename(columns={'kshift (rad/m)': 'kshift (rad/µm)'}, inplace=True)
        
        dispersion = dispersion[dispersion['k (rad/µm)'] >= self.data['kMin']]
        dispersion = dispersion[dispersion['k (rad/µm)'] <= self.data['kMax']]
        dispersion.drop(columns=['m'], inplace=True)
        
        dispersion.to_csv(f'simulation_data/{self.task_id}/dispersion_data.csv')
        
        print('Dispersion calculated successfully!')
        
        self.json_helper.set_parameter('status', 'Dispersion calculation successful!')
        
        return dispersion
    
    def data_parser(self):
        for key in self.data.keys():
            if 'Axis' in key:
                self.data[key] = axis_to_index[self.data[key]]
                continue
            try:
                self.data[key] = float(self.data[key])
            except:
                continue

axis_to_index = {
    'x': [1, 0, 0], 
    'y': [0, 1, 0], 
    'z': [0, 0, 1] 
}