import os

class PWStruct:
    def __init__(self):
        self.pw_name = "user"
        self.pw_dir = os.path.expanduser("~")
        self.pw_uid = 1000
        self.pw_gid = 1000

def getpwuid(uid):
    return PWStruct()

def getpwnam(name):
    return PWStruct()
