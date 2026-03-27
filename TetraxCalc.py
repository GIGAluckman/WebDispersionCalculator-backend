import tetrax as tx
import os
import time
from df_manipulation import *

SIMULATION_DATA_PATH = os.getenv('SIMULATION_DATA_PATH', 'simulation_data')

class TetraxCalc:
    def __init__(self, data, id, json_helper, num_cpus=-1):
        
        self.simulation_path = os.path.join(SIMULATION_DATA_PATH, str(id))
        if not os.path.exists(self.simulation_path):
            os.makedirs(self.simulation_path)
        
        self.task_id = id
        self.data = data
        self.geometry = data['chosenGeometry']
        self.num_cpus = num_cpus
        self.data_parser()
        self.json_helper = json_helper
        self._field_names = ["dipole", "exchange", "zeeman", "uniaxial_anisotropy"]
        
    def set_geometry(self):
        if self.geometry == 'Waveguide':
            mesh = tx.geometries.waveguide.rectangular(
                width=self.data['width'],
                thickness=self.data['thickness'],
                cell_size_width=int(self.data.get('dWidth', 5)),
                cell_size_thickness=int(self.data.get('dThick', 5)),
            )
            
            print("Mesh created for waveguide geometry")
            print("Mesh width:", self.data['width'])
            print("Mesh thickness:", self.data['thickness'])
            print("Mesh cell size width:", self.data.get('dWidth', 5))
            print("Mesh cell size thickness:", self.data.get('dThick', 5))
            
        elif self.geometry == 'Plane Film':
            mesh = tx.geometries.layer.monolayer(
                thickness=self.data['thickness'],
                cell_size=int(self.data.get('dThick', 5)),
            )
            
            print("Mesh created for plane film geometry")
            print("Mesh thickness:", self.data['thickness'])
            print("Mesh cell size:", self.data.get('dThick', 5))
             
        elif self.geometry == 'Wire':
            mesh = tx.geometries.waveguide.round_wire(
                radius=self.data['radius'],
                cell_size=int(self.data.get('dRadius', 5)),
            )
            
            print("Mesh created for wire geometry")
            print("Mesh radius:", self.data['radius'])
            print("Mesh cell size:", self.data.get('dRadius', 5))
            
        self.sample = tx.Sample(mesh, name=self.simulation_path)
        
    def set_material(self):
        self.sample.material['Msat'] = float(self.data['saturationMagnetization'])
        self.sample.material['Aex'] = float(self.data['exchangeStiffness']) * 1e-12
        self.sample.material['alpha'] = float(self.data['GilbertDamping'])
        if 'anisotropyConstant' in self.data.keys():
            self.sample.material['Ku1'] = float(self.data['anisotropyConstant'])
            self.sample.material['e_u'] = self.data['anisotropyAxis']
            
        print('Material set with parameters:')
        print(f"Msat: {self.sample.material['Msat'].average} A/m")
        print(f"Aex: {self.sample.material['Aex'].average} J/m")
        print(f"Ku1: {self.sample.material['Ku1'].average} J/m^3")
        print(f"e_u: {self.sample.material['e_u'].average}")
        
    def calculate_dispersion(self):
        start_dispersion_time = time.time()
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
            num_cpus=self.num_cpus,
            num_modes=int(self.data['numberOfModes']),
            kmin=self.data['kMin'] * 1e6,
            kmax=self.data['kMax'] * 1e6, 
            num_k=int(self.data.get('numberOfK', 11)))
        
        if if_nan(dispersion.spectrum_dataframe):
            dispersion = dataframe_polish(dispersion.spectrum_dataframe, self.data['kMin'], self.data['kMax'], self.task_id)
            self.json_helper.set_parameter('status', 'NaN found in dispersion calculation!')
            self.json_helper.set_parameter('error', 2)
            return dispersion, 1
        
        dispersion.linewidths()
        
        dispersion = dispersion.spectrum_dataframe
        
        dispersion = lifetime(dispersion)
        dispersion = group_velocity(dispersion)
        dispersion = propagation_length(dispersion)
        dispersion = dataframe_polish(dispersion, self.data['kMin'], self.data['kMax'], self.task_id)
        print('Dispersion calculated successfully!')
        
        self.json_helper.set_parameter('status', 'Dispersion calculation successful!')
        end_dispersion_time = time.time()
        calc_time = round(end_dispersion_time - start_dispersion_time, 3)
        self.json_helper.set_parameter('time', calc_time)
        
        self.save_field_data()
        return dispersion, 0
    
    def save_field_data(self):
        field_terms = []
        for field_name in self._field_names:
            field_data = self.sample.get_field(field_name)
            field_terms.append(field_data)
            self.sample.field_to_file(field_data, os.path.join(self.simulation_path, f'{field_name}.vtk'))
        
        total_field = sum(field_terms)
        self.sample.field_to_file(total_field, os.path.join(self.simulation_path, f'total.vtk'))
            
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