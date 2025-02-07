import json

class JSONHelper:
    def __init__(self, db_path):
        self.db_path = db_path
        
    def create_db(self, data):
        data['status'] = 'pending'
        data['progress'] = 0
        data_to_json = {'data': data}
        
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump(data_to_json, f, ensure_ascii=False, indent=4)

    def set_parameter(self, name, value):
        with open(self.db_path) as f:
            data = json.load(f)
        
        data['data'][name] = value
        
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            
    def get_parameter(self, name):
        with open(self.db_path) as f:
            data = json.load(f)
        return data['data'][name]