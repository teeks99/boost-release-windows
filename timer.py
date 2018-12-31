import datetime
import sys

class Timer(object):
    def __init__(self, name):
        self.name = name
        self.running = False
        self.complete = False

    def start(self):
        self.running = True
        self.complete = False
        self.start = datetime.datetime.now()

    def stop(self):
        self.stop = datetime.datetime.now()
        self.running = False
        self.complete = True
        self.elapsed = self.stop - self.start

    def output(self, file=sys.stdout):
        opened_file = False
        if isinstance(file, basestring):
            file = open(file, "a")
            opened_file = True
        file.write("--- " + self.name + " ---\n")
        file.write("    start: " + str(self.start) + "\n")
        if not self.complete:
            point = datetime.datetime.now()
            file.write("    point:  " + str(point) + "\n")
            file.write("    elapsed: " + str(point - self.start) + "\n")
        else:
            file.write("    stop:  " + str(self.stop) + "\n")
            file.write("    elapsed: " + str(self.elapsed) + "\n")

        if opened_file:
            file.close()
