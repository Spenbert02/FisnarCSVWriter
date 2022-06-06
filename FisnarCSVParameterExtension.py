import copy
import numpy
import os.path
import sys
import time
from typing import Optional, Union, List

from cura.BuildVolume import BuildVolume
from cura.CuraApplication import CuraApplication

from PyQt5.QtCore import QObject, QUrl, pyqtSlot, pyqtProperty
from PyQt5.QtQml import QQmlComponent, QQmlContext

from UM.Application import Application
from UM.Extension import Extension
from UM.Logger import Logger
from UM.Math.Polygon import Polygon
from UM.PluginRegistry import PluginRegistry
from UM.Scene.Iterator.BreadthFirstIterator import BreadthFirstIterator

from .SerialUploader import SerialUploader
from .Converter import Converter

# needed for all 'manual' imports
import importlib
plugin_folder_path = os.path.dirname(__file__)

# importing pyperclip
pyperclip_path = os.path.join(plugin_folder_path, "pyperclip", "src", "pyperclip", "__init__.py")
spec_2 = importlib.util.spec_from_file_location("pyperclip", pyperclip_path)
pyperclip_module = importlib.util.module_from_spec(spec_2)
sys.modules["pyperclip"] = pyperclip_module
spec_2.loader.exec_module(pyperclip_module)
import pyperclip


class FisnarCSVParameterExtension(QObject, Extension):


    # for factory methods. This will be set to the instance of this class once initialized.
    # this class is only instantiated once, when Cura first opens.
    _instance = None


    def __init__(self, parent=None):
        # calling necessary super methods.
        QObject.__init__(self, parent)
        Extension.__init__(self)

        # factory object creation
        if FisnarCSVParameterExtension._instance is not None:  # if object has already been instantiated
            Logger.log("e", "****** FisnarCSVParameterExtension instantiated more than once")
        else:  # first time instantiating object
            # Logger.log("i", "****** FisnarCSVParameterExtension instantiated for the first time")  # test
            FisnarCSVParameterExtension._instance = self

        # signal to update disallowed areas whenever build plate activity is changed
        # this is called a shit load. It works for now, but maybe look for a cleaner solution in the future
        CuraApplication.getInstance().activityChanged.connect(self.resetDisallowedAreas)

        # print area parameters
        self.fisnar_x_min = 0.0
        self.fisnar_x_max = 200.0
        self.fisnar_y_min = 0.0
        self.fisnar_y_max = 200.0
        self.fisnar_z_max = 100.0

        # output correlations
        self.num_extruders = None
        self.extruder_1_output = None
        self.extruder_2_output = None
        self.extruder_3_output = None
        self.extruder_4_output = None

        # setting up menus
        self.setMenuName("Fisnar Actions")
        self.addMenuItem("Print Surface Definition", self.showParameterEntryWindow)
        self.addMenuItem("Output Setup", self.showOutputEntryWindow)
        self.addMenuItem("Upload Commands to Fisnar", self.showSerialUploadWindow)

        # 'lazy loading' windows, so can be called later.
        self.parameter_entry_window = None
        self.output_entry_window = None
        self.serial_upload_window = None
        self.serial_upload_error_window = None
        self.serial_upload_success_window = None

        self.most_recent_fisnar_commands = None  # for passing to serial uploader object
        self.serial_uploader = SerialUploader()
        self.conversion_mode = Converter.IO_CARD  # for FisnarCSVWriter to grab to convert

        # writes to logger when something happens (TODO figure out when this is called, although it doesn't really matter).
        # ya pretty sure this is totally irrelevant but I'm gonna leave it
        Application.getInstance().mainWindowChanged.connect(self.logMessage)


    @classmethod
    def getInstance(cls):
        # factory method
        return cls._instance


    def resetDisallowedAreas(self):
        # turning fisnar coords to gcode coords (fisnar coords are inverted in each direction)
        # the following inversion is based on a (200, 200, 100) fisnar print area. Note: the printer in cura needs to be set up as such to look the best
        # also, the min and max nomenclatures are flipped (because the directions are inverted)
        # NOTE: for some reason, the build volume class takes the origin of the build plate to be at the center
        # NOTE: this code assumes a build volume x-y dimension of (200, 200). Any integers seen in this code are based off of this assumption

        # adding disallowed areas to each BuildVolume object
        scene = Application.getInstance().getController().getScene()
        for node in BreadthFirstIterator(scene.getRoot()):
            if isinstance(node, BuildVolume):
                # getting build volume dimensions and original disallowed areas, for documentation
                orig_disallowed_areas = node.getDisallowedAreas()
                x_dim = node.getWidth()  # NOTE: I think width corresponds to x, not sure
                y_dim = node.getDepth()  # NOTE: same as above comment. I think this is right

                # converting coord system from fisnar to build volume coord system
                bv_x_min = 100 - self.fisnar_x_max
                bv_x_max = 100 - self.fisnar_x_min
                bv_y_min = self.fisnar_y_min - 100
                bv_y_max = self.fisnar_y_max - 100
                new_z_dim = self.fisnar_z_max

                # establishing new dissalowed areas (list of Polygon objects)
                new_disallowed_areas = []
                new_disallowed_areas.append(self.HandledPolygon([[-100, -100], [-100, 100], [bv_x_min, bv_y_max], [bv_x_min, bv_y_min]]))
                new_disallowed_areas.append(self.HandledPolygon([[-100, 100], [100, 100], [bv_x_max, bv_y_max], [bv_x_min, bv_y_max]]))
                new_disallowed_areas.append(self.HandledPolygon([[100, 100], [100, -100], [bv_x_max, bv_y_min], [bv_x_max, bv_y_max]]))
                new_disallowed_areas.append(self.HandledPolygon([[100, -100], [-100, -100], [bv_x_min, bv_y_min], [bv_x_max, bv_y_min]]))

                # getting rid of old polygons in old list
                iter = 0
                while iter < len(orig_disallowed_areas):
                    if isinstance(orig_disallowed_areas[iter], self.HandledPolygon):
                        orig_disallowed_areas.pop(iter)
                    else:
                        iter += 1

                # updating original list with new list
                for poly in new_disallowed_areas:
                    orig_disallowed_areas.append(poly)

                # setting new disallowed areas and rebuilding (not sure if the rebuild is necessary)
                node.setDisallowedAreas(orig_disallowed_areas)
                node.setHeight(new_z_dim)
                node.rebuild()

                # logging updated disallowed areas, tests
                # Logger.log("i", "****** build volume disallowed areas have been reset")
                # Logger.log("i", "****** original disallowed areas: " + str(orig_disallowed_areas))
                # Logger.log("i", "****** new disallowed areas: " + str(new_disallowed_areas))


    def logMessage(self):
        # logging message when one of the windows is opened
        Logger.log("i", "Fisnar parameter or output entry window opened")


    @pyqtSlot(bool)
    def setOutputMode(self, uses_io_card):
        # called by qml when checkbox status changes to change the conversion mode
        # which whill eventually be grabbed by FisnarCSVWriter for conversion
        Logger.log("i", str(uses_io_card) + ", " + str(type(uses_io_card)))
        if uses_io_card:
            self.conversion_mode = Converter.IO_CARD
        else:
            self.conversion_mode = Converter.NO_IO_CARD


    @pyqtProperty(str)
    def getXMin(self):
        # called by qml to get the min x coord
        return str(self.fisnar_x_min)


    @pyqtProperty(str)
    def getXMax(self):
        # called by qml to get the max x coord
        return str(self.fisnar_x_max)


    @pyqtProperty(str)
    def getYMin(self):
        # called by qml to get the min y coord
        return str(self.fisnar_y_min)


    @pyqtProperty(str)
    def getYMax(self):
        # called by qml to get the max y coord
        return str(self.fisnar_y_max)


    @pyqtProperty(str)
    def getZMax(self):
        # called by qml to get the max z coord
        return str(self.fisnar_z_max)


    @pyqtSlot(str, str)
    def setCoord(self, attribute, coord_val):
        # slot for qml to set the value of one of the home coordinates
        setattr(self, attribute, float(coord_val))  # validation occurs in the qml file
        # Logger.log("i", "***** " + str(attribute) + " set to " + str(getattr(self, attribute)) + " *****")  # test
        self.resetDisallowedAreas()  # updating disallowed areas on the build plate


    @pyqtProperty(int)
    def getNumExtruders(self):
        # called by qml to get the number of active extruders in Cura
        self.num_extruders = len(Application.getInstance().getExtrudersModel()._active_machine_extruders)
        # Logger.log("i", "***** number of extruders: " + str(self.num_extruders))  # test
        return self.num_extruders


    @pyqtProperty(str)
    def getExtruder1Output(self):
        # called by qml to get the output number associated with extruder 1
        return str(self.extruder_1_output)


    @pyqtProperty(str)
    def getExtruder2Output(self):
        # called by qml to get the output number associated with extruder 2
        return str(self.extruder_2_output)


    @pyqtProperty(str)
    def getExtruder3Output(self):
        # called by qml to get the output number associated with extruder 3
        return str(self.extruder_3_output)


    @pyqtProperty(str)
    def getExtruder4Output(self):
        # called by qml to get the output number associated with extruder 4
        return str(self.extruder_4_output)


    @pyqtSlot(str, str)
    def setExtruderOutput(self, attribute, output_val):
        # slot for qml to set the output associated with one of the extruders
        if str(output_val) == "None":  # None value set
            setattr(self, attribute, None)
        else:  # actual output value
            setattr(self, attribute, int(output_val))
        Logger.log("i", "***** attribute '" + str(attribute) + "' set to " + str(getattr(self, attribute)) + "(" + str(type(getattr(self, attribute))) + ")")  # test


    @pyqtProperty(str)
    def getSerialUploadWindowText(self):
        # gateway for qml to get the text to show for the initial serial upload dialog
        return "The most recent Fisnar CSV file you saved will be uploaded to the Fisnar. Press OK to continue, or Cancel to cancel."


    @pyqtProperty(str)
    def getSerialErrorMsg(self):
        # gateway for qml to get the error message from the SerialUploader object
        return "Error occured while uploading: " + str(self.serial_uploader.getInformation())


    @pyqtSlot()
    def startSerialUpload(self):
        # called by qml to start the serial uploading process, starts the upload
        # process using the SerialUploader member object
        Logger.log("i", "serial upload process started")

        self.serial_uploader.setCommands(self.most_recent_fisnar_commands)  # setting commands
        successful_conversion = self.serial_uploader.uploadCommands()  # uploading

        if not successful_conversion:
            self.showFailedSerialUploadWindow()
        else:
            self.showSuccessfulSerialUploadWindow()

        self.serial_uploader.setInformation(None)  # clearing error info in case there is another upload


    @pyqtSlot()
    def cancelSerialUpload(self):
        # called by qml if the user presses the cancel button on the main
        # serial upload screen. Doesn't do anything besides logging
        Logger.log("i", "serial upload process cancelled")


    def showParameterEntryWindow(self):
        # Logger.log("i", "parameter entry window called")  # test
        if not self.parameter_entry_window:  # ensure a window isn't already created
            self.parameter_entry_window = self._createDialogue("parameter_entry.qml")
        self.parameter_entry_window.show()


    def showOutputEntryWindow(self):
        # Logger.log("i", "Output Entry Window Called")  # test
        if not self.output_entry_window:
            self.output_entry_window = self._createDialogue("output_entry.qml")
        self.output_entry_window.show()


    def showSerialUploadWindow(self):
        Logger.log("i", "serial upload window called")  # test
        if not self.serial_upload_window:
            self.serial_upload_window = self._createDialogue("serial_upload_window.qml")
        self.serial_upload_window.show()


    def showFailedSerialUploadWindow(self):
        # pop up to show if the serial upload fails, with information about the
        # error given by the SerialUploader object
        # Logger.log("i", "failed serial upload window called")  # test
        if not self.serial_upload_error_window:
            self.serial_upload_error_window = self._createDialogue("serial_upload_error.qml")
        self.serial_upload_error_window.show()


    def showSuccessfulSerialUploadWindow(self):
        # pop up to show if the serial upload is successful
        # Logger.log("i", "successful serial upload window called")  # test
        if not self.serial_upload_success_window:
            self.serial_upload_success_window = self._createDialogue("serial_upload_success.qml")
        self.serial_upload_success_window.show()


    def _createDialogue(self, qml_file_name):
        # Logger.log("i", "***** Fisnar CSV Writer dialogue created")  # test
        qml_file_path = os.path.join(PluginRegistry.getInstance().getPluginPath(self.getPluginId()), "resources", "qml", qml_file_name)
        component = Application.getInstance().createQmlComponent(qml_file_path, {"main": self})
        return component


    class HandledPolygon(Polygon):
        # class that extends Polygon object so a polygon can be checked if it has been set in this extension or by something else
        # to see if a 'Polygon' object is HandledPolygon, just check the instance's type
        def __init__(self, points: Optional[Union[numpy.ndarray, List]] = None):
            super().__init__(points)
