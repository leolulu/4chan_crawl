import pickle
import threading
import os


class PickleHandler:
    '''
    p = pickle_handler(file_path)

    p.dump(byte_obj)
    p.load()
    '''

    def __init__(self, file_path):
        self.lock = threading.Lock()
        self.file_path = file_path
        if not os.path.exists(file_path):
            with open(file_path, 'wb') as f:
                pickle.dump(set(), f)

    def dump(self, byte_obj):
        with self.lock:
            with open(self.file_path, 'wb') as f:
                pickle.dump(byte_obj, f)

    def load(self):
        with self.lock:
            with open(self.file_path, 'rb') as f:
                return pickle.load(f)
