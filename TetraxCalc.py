import tetrax as tx

class TetraxCalc:
    def __init__(self, data, id):
        
        self.task_id = id
        self.data = data
        self.geometry = data['chosenGeometry']
        self.data_parser()
        
    def set_geometry(self):
        if self.geometry == 'Waveguide':
            self.sample = tx.create_sample(f'simulation_data/{self.task_id}', geometry='waveguide')
            mesh = tx.geometries.rectangle_cross_section(
                self.data['width'], 
                self.data['thickness'], 
                self.data['width']/20, 
                self.data['thickness']/5)
            
        elif self.geometry == 'Plane Film':
            self.sample = tx.create_sample(f'simulation_data/{self.task_id}', geometry='layer')
            mesh = tx.geometries.monolayer_line_trace(self.data['thickness'], self.data['thickness']/10)
            
        elif self.geometry == 'Wire':
            self.sample = tx.create_sample(f'simulation_data/{self.task_id}', geometry='waveguide')
            mesh = tx.geometries.round_wire_cross_section(self.data['radius'], self.data['radius']/5)
        
        self.sample.set_geom(mesh)
        
    def set_material(self):
        self.sample.Msat = float(self.data['saturationMagnetization'])
        self.sample.Aex = float(self.data['exchangeStiffness'])
        if 'anisotropyConstant' in self.data.keys():
            self.sample.Ku1 = float(self.data['anisotropyConstant'])
            self.sample.e_u = self.data['anisotropyAxis']
            
        print('Material set with parameters:')
        print(f'Msat: {self.sample.Msat.mean()} A/m')
        print(f'Aex: {self.sample.Aex.mean()} J/m')
        print(f'Ku1: {self.sample.Ku1} J/m^3')
        print(f'e_u: {self.sample.e_u}')
        
    def calculate_dispersion(self):
        
        self.set_geometry()
        self.set_material()
        
        self.sample.mag = self.data['fieldAxis']
        exp = tx.create_experimental_setup(self.sample)
        exp.Bext = [i*self.data['extField']/1e3 for i in self.data['fieldAxis']]
        
        print('External field set to:', exp.Bext[0], 'T')
        
        nr_trial = 0
        success = False
        while (not(success) and (nr_trial < 5)):
            success = exp.relax(tol=1e-13,continue_with_least_squares=True,verbose=False)
            nr_trial += 1
        if success:
            print('Relaxation successful')
        else:
            print('Relaxation failed')
        dispersion = exp.eigenmodes(
            num_cpus=-1,
            num_modes=self.data['nModes'],
            kmin=self.data['kMin'] * 1e6,
            kmax=self.data['kMax'] * 1e6, Nk=11)
        
        dispersion['k (rad/m)'] = dispersion['k (rad/m)'] / 1e6
        
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