import copy
from .FisnarCommands import FisnarCommands
from .gcodeBuddy.marlin import Command
from .PrinterAttributes import PrintSurface
from .UltimusV import UltimusV

from UM.Logger import Logger


class Converter:
    # class that facilitates the translation of commands between gcode and
    # fisnar commands in several different formats
    #

    XYZ_COMMANDS = ("Dummy Point", "Line Start", "Line Passing", "Line End")

    def __init__(self):
        self.gcode_commands_str = None  # gcode commands as string separated by \n characters
        self.gcode_commands_lst = None  # gcode commands as a list of Command objects
        self.last_converted_fisnar_commands = None  # the last converted fisnar command list

        self.print_surface = None  # type: PrintSurface
        self.continuous_extrusion = False

        self.information = None  # for error reporting

    def setInformation(self, info_str):
        # set error information (takes one str parameter)
        self.information = str(info_str)  # converting to str just to be safe

    def getInformation(self):
        # get error information as string describing error that occured
        if self.information is None:
            return "<no error found>"
        else:
            return str(self.information)

    def setPrintSurface(self, print_surface):
        # set the Fisnar print surface coordinates
        self.print_surface = print_surface

    def getPrintSurface(self):
        # get the Fisnar print surface area as an array of coords in the form:
        # [x min, x max, y min, y max, z max]
        return self.print_surface

    def setContinuousExtrusion(self, state):
        # set the continuous extrusion state. If state==True, then continuous extruding is used.
        # if state == False, then continuous extruding is not used
        self.continuous_extrusion = state

    def getContinuousExtrusion(self):
        # get the continuous extrusion state (True for continuous extrusion, False if not)
        return self.continuous_extrusion

    def setGcode(self, gcode_str):
        # sets the gcode string (and gcode list)
        self.gcode_commands_str = gcode_str
        self.gcode_commands_lst = Converter.getStrippedCommands(gcode_str.split("\n"))

    def getFisnarCommands(self):
        # get the fisnar command list from the last set gcode commands and settings.
        # returns False if an error occurs, and sets its information to an error description

        # ensuring gcode commands exist
        if self.gcode_commands_lst is None:
            self.setInformation("internal error: in getFisnarCommands(), gcode_commands_lst is None")
            return False

        # converting and interpreting command output
        fisnar_commands = self.convertCommands()
        if fisnar_commands is False:  # error - error info will already be set by convert() function
            return False

        # confirming that all coordinates are within the build volume
        if not self.boundaryCheck(fisnar_commands):
            self.setInformation("coordinates fell outside user-specified print surface after conversion; if using build plate adhesion, see the 'preview' tab to ensure all material is within the print surface")
            return False

        self.last_converted_fisnar_commands = fisnar_commands
        return fisnar_commands

    def convertCommands(self):
        # convert gcode to fisnar command 2d list. Assumes the extruder outputs given are valid.
        # returns False if there aren't enough gcode commands to deduce any Fisnar commands.
        # Works for both i/o card and non i/o card commands

        # useful information for the conversion process
        first_relevant_command_index = Converter.getFirstPositionalCommandIndex(self.gcode_commands_lst)
        last_relevant_command_index = Converter.getLastExtrudingCommandIndex(self.gcode_commands_lst)

        # in case there isn't enough commands (this should never happen in slicer output)
        if first_relevant_command_index is None or last_relevant_command_index is None:
            self.setInformation("not enough gcode commands to deduce Fisnar commands")
            return False

        # default fisnar initial commands
        fisnar_commands = [["Line Speed", 30], ["SET ME AFTER CONVERTING COORD SYSTEM"]]

        # finding first extruder used in gcode
        curr_extruder = 0
        for command in self.gcode_commands_lst:
            if command.get_command()[0] == "T":
                curr_extruder = int(command.get_command()[1])
                break

        curr_pos = [0, 0, 0]
        curr_speed = 30.0
        for i in range(len(self.gcode_commands_lst)):
            command = self.gcode_commands_lst[i]

            # line speed change and converting from mm/min to mm/sec
            if command.has_param("F") and (command.get_param("F") / 60) != curr_speed:
                curr_speed = command.get_param("F") / 60
                fisnar_commands.append(["Line Speed", curr_speed])

            if first_relevant_command_index <= i <= last_relevant_command_index:  # command needs to be converted
                if command.get_command() in ("G0", "G1"):
                    new_commands = Converter.g0g1WithIO(command, curr_extruder + 1, curr_pos)
                    for command in new_commands:
                        fisnar_commands.append(command)
                elif command.get_command() in ("G2", "G3"):
                    pass  # might implement eventually. probably not, these are _rarely_ used.
                elif command.get_command() == "G90":
                    pass  # assuming all commands are absolute coords for now.
                elif command.get_command() == "G91":
                    pass  # assuming all commands are absolute coords for now.
                elif command.get_command()[0] == "T":
                    curr_extruder = int(command.get_command()[1])

        # turning off necessary outputs
        gcode_outputs = Converter.getOutputsInFisnarCommands(fisnar_commands)
        Logger.log("d", "gcode outputs: " + str(gcode_outputs))
        for i in range(4):
            if gcode_outputs[i]:
                fisnar_commands.append(["Output", i + 1, 0])
        fisnar_commands.append(["End Program"])

        # inverting and shifting coordinate system from gcode to fisnar, then putting home travel command
        Converter.invertCoords(fisnar_commands, self.print_surface.getZMax())

        # put home coordinates into home dummy point
        fisnar_commands[1] = ["Dummy Point", self.print_surface.getXMin(), self.print_surface.getYMin(), self.print_surface.getZMax()]

        # removing redundant output and line speed commands
        Converter.optimizeFisnarOutputCommands(fisnar_commands)
        Converter.optimizeLineSpeedCommands(fisnar_commands)  # ensures no consectuive line speed commands

        # Logger.log("d", f"Converter: {self.continuous_extrusion}")
        if self.continuous_extrusion:
            num_outputs = Converter.getOutputsInFisnarCommands(fisnar_commands).count(True)
            # Logger.log("d", f"converter num_outputs: {num_outputs}")
            if num_outputs == 1:  # only one extruder. keep printing continuously
                first_on_command_ind = None  # first extruding command
                last_off_command_ind = None  # last command that turns off extrusion (first output off after the last output on)

                for i in range(len(fisnar_commands)):
                    if fisnar_commands[i][0] == "Output" and fisnar_commands[i][2] == 1:  # output on
                        if first_on_command_ind is None:  # first extruding command
                            first_on_command_ind = i
                        
                        j = i + 1
                        next_off_found = False
                        while j < len(fisnar_commands) and not next_off_found:  # getting next output off after output on
                            if fisnar_commands[j][0] == "Output" and fisnar_commands[j][2] == 0:
                                last_off_command_ind = j
                                next_off_found = True
                            j += 1
            
                # removing all output off and on commands in between the first on and last off commands
                i = first_on_command_ind + 1  # starting at command after first on command
                while i < last_off_command_ind:  # ending before last off command
                    if fisnar_commands[i][0] == "Output":
                        del fisnar_commands[i]
                        last_off_command_ind -= 1  # bumping back one index
                    else:  # not output
                        i += 1 
            else:  # more than one output
                pass  # TODO: implement this                  

        return fisnar_commands

    def boundaryCheck(self, fisnar_commands):
        # check that all coordinates are within the user specified area. If ANY
        # coordinates fall outside the volume, False will be returned - if all
        # coordinates fall within the volume, True will be returned
        for command in fisnar_commands:
            if command[0] in Converter.XYZ_COMMANDS:
                if not (self.print_surface.getXMin() <= command[1] <= self.print_surface.getXMax()):
                    Logger.log("e", f"command found outside user-defined build volume: {str(command)}")
                    return False
                if not (self.print_surface.getYMin() <= command[2] <= self.print_surface.getYMax()):
                    Logger.log("e", f"command found outside user-defined build volume: {str(command)}")
                    return False
                if not (0 <= command[3] <= self.print_surface.getZMax()):
                    Logger.log("e", f"command found outside user-defined build volume: {str(command)}")
                    return False
        return True  # functioned hasn't returned False, so all good

    @staticmethod
    def optimizeLineSpeedCommands(fisnar_commands):
        i = len(fisnar_commands) - 1
        while i >= 0:
            if fisnar_commands[i][0] == "Line Speed":
                i -= 1
                while fisnar_commands[i][0] == "Line Speed":
                    fisnar_commands.pop(i)
                    i -= 1
            else:
                i -= 1

    @staticmethod
    def g0g1NoIO(command, next_command, curr_pos):
        # take a command, the command after it, and the position before the
        # current command
        command_type = None
        if command.has_param("E") and command.get_param("E") > 0:
            if next_command.has_param("E") and next_command.get_param("E") > 0:  # E -> E
                command_type = "Line Passing"
            else:  # E -> no E
                command_type = "Line End"
        else:
            if next_command.has_param("E") and next_command.get_param("E") > 0:  # no E -> E
                command_type = "Line Start"
            else:  # no E -> no E
                command_type = "Dummy Point"

        # determining command positions (and updating current position)
        if command.has_param("X"):
            curr_pos[0] = command.get_param("X")
        if command.has_param("Y"):
            curr_pos[1] = command.get_param("Y")
        if command.has_param("Z"):
            curr_pos[2] = command.get_param("Z")

        # returning command
        return [command_type, curr_pos[0], curr_pos[1], curr_pos[2]]

    @staticmethod
    def g0g1WithIO(command, curr_output, curr_pos):
        # turn a g0 or g1 command into a list of the corresponding fisnar commands
        # update the given curr_pos list
        ret_commands = []

        if command.has_param("E") and command.get_param("E") > 0:  # turn output on
            ret_commands.append(["Output", curr_output, 1])
        else:  # turn output off
            ret_commands.append(["Output", curr_output, 0])

        x, y, z = curr_pos[0], curr_pos[1], curr_pos[2]
        if command.has_param("X"):
            x = command.get_param("X")
        if command.has_param("Y"):
            y = command.get_param("Y")
        if command.has_param("Z"):
            z = command.get_param("Z")

        curr_pos[0], curr_pos[1], curr_pos[2] = x, y, z
        ret_commands.append(["Dummy Point", x, y, z])

        return ret_commands

    @staticmethod
    def getOutputsInFisnarCommands(commands):
        # return a list of bools representing the outputs in a given list of
        # fisnar commands
        outputs = [False, False, False, False]
        for command in commands:
            if command[0] == "Output":
                outputs[int(command[1]) - 1] = True
        return outputs

    @staticmethod
    def getStrippedCommands(gcode_lines):
        # convert a list of gcode lines (in string form) to a list of gcode Command objects
        # commands are subsequently stripped of comments and empty lines. Only commands are interpreted
        ret_command_list = []  # list to hold Command objects
        for line in gcode_lines:
            line = line.strip()  # removing whitespace from both ends of string
            if len(line) > 0 and line[0] != ";":  # only considering non comment and non empty lines
                if ";" in line:
                    line = line[:line.find(";")]  # removing comments
                line = line.strip()  # removing whitespace from both ends of string
                ret_command_list.append(Command(line))
        return ret_command_list

    @staticmethod
    def getFirstExtrudingCommandIndex(gcode_commands):
        # get the index of the first g0/g1 command that extrudes.
        # this command must be g0/g1, have an x or y or z parameter, and have a non-zero e parameter.
        for i in range(len(gcode_commands)):
            command = gcode_commands[i]
            if command.get_command() in ("G0", "G1"):
                if command.has_param("X") or command.has_param("Y") or command.has_param("Z"):
                    if command.has_param("E") and (command.get_param("E") > 0):
                        return i
        return None  # no extruding commands. Don't know how a gcode file wouldn't have an extruding command but just in case

    @staticmethod
    def getFirstPositionalCommandIndex(gcode_commands):
        # get the index of the command that is the start of the first extruding movement.
        # this is the last command before the first extruding movement command that doesn't extrude
        # this command must have no e parameter (or zero e parameter), and have an x and y and z parameter
        first_extruding_index = Converter.getFirstExtrudingCommandIndex(gcode_commands)
        if first_extruding_index is None:
            return None  # shouldn't ever happen in a reasonable gcode file

        for i in range(first_extruding_index, -1, -1):
            command = gcode_commands[i]
            if command.get_command() in ("G0", "G1"):
                if command.has_param("X") and command.has_param("Y") and command.has_param("Z"):
                    if not (command.has_param("E") and command.get_param("E") > 0):
                        return i
        return None  # this could be for a variety of reasons, some of which aren't that unlikely. This way of doing things is kind of ghetto. Ultimately, a more sophisticated solution should be enacted.

    @staticmethod
    def getLastExtrudingCommandIndex(gcode_commands):
        # get the last command that extrudes material - the last command that needs to be converted.
        # this command must have an x and/or y and/or z parameter, and have a nonzero e parameter
        for i in range(len(gcode_commands) - 1, -1, -1):
            command = gcode_commands[i]
            if command.get_command() in ("G0", "G1"):
                if command.has_param("X") or command.has_param("Y") or command.has_param("Z"):
                    if command.has_param("E") and command.get_param("E") > 0:
                        return i
        return None  # this should never happen in any reasonable gcode file.

    @staticmethod
    def optimizeFisnarOutputCommands(fisnar_commands):
        # remove any redundant output commands from fisnar command list
        for output in range(1, 5):  # for each output (integer from 1 to 4)
            output_state = None
            i = 0
            while i < len(fisnar_commands):
                if fisnar_commands[i][0] == "Output" and fisnar_commands[i][1] == output:  # is an output 1 command
                    if output_state is None:  # is the first output 1 command
                        output_state = fisnar_commands[i][2]
                        i += 1
                    elif fisnar_commands[i][2] == output_state:  # command is redundant
                        fisnar_commands.pop(i)
                    else:
                        output_state = fisnar_commands[i][2]
                        i += 1
                else:
                    i += 1

    @staticmethod
    def invertCoords(commands, z_dim):
        # invert all coordinate directions for dummy points (modifies the given list)
        for i in range(len(commands)):
            if commands[i][0] in Converter.XYZ_COMMANDS:
                commands[i][1] = 200 - commands[i][1]
                commands[i][2] = 200 - commands[i][2]
                commands[i][3] = z_dim - commands[i][3]

    @staticmethod
    def numNestedElements(nested_list):
        # get the number of commands in a list of segmented command coords - the
        # commands counted are dummy point and line speed (not ouput)
        ret_sum = 0
        for command in nested_list:
            if isinstance(command[0], list):
                for j in range(len(command) - 1):
                    ret_sum += 1
            else:
                ret_sum += 1
        return ret_sum

    @staticmethod
    def segmentFisnarCommands(fisnar_commands):
        # get a 'segmented' list of fisnar commands, with dummy point sequences
        # of common extrusion state grouped together and all output commands
        # removed
        ret_commands = []
        temp_commands = []
        for command in fisnar_commands:
            if command[0] == "Dummy Point":
                temp_commands.append(copy.deepcopy(command))
            elif command[0] in ("Line Speed", "Output", "End Program"):
                if temp_commands != []:
                    ret_commands.append(temp_commands)
                    temp_commands = []
                ret_commands.append(command)

        if temp_commands != []:  # cleaning up if any dummy points left over
            ret_commands.append(temp_commands)
            temp_commands = []

        output_states = [0, 0, 0, 0]
        for i in range(len(ret_commands)):
            if isinstance(ret_commands[i][0], list):  # is a dummy point sublist
                ret_commands[i].append([output_states[0], output_states[1], output_states[2], output_states[3]])
            elif ret_commands[i][0] == "Output":  # isn't a sublist, so check if is an output
                output_states[ret_commands[i][1] - 1] = ret_commands[i][2]

        # deleting output commands
        i = 0
        while i < len(ret_commands):
            if ret_commands[i][0] == "Output":
                ret_commands.pop(i)
            else:
                i += 1

        return ret_commands

    @staticmethod
    def fisnarCommandsToCSVString(fisnar_commands):
        # turn a 2d list of fisnar commands into a csv string
        ret_string = ""
        for i in range(len(fisnar_commands)):
            for j in range(len(fisnar_commands[i])):
                ret_string += str(fisnar_commands[i][j])
                if j == len(fisnar_commands[i]) - 1:
                    ret_string += "\n"
                else:
                    ret_string += ","

        return ret_string

    @staticmethod
    def fisnarCommandsToBytes(fisnar_commands, continuous_extrusion):
        # from a 2d-array of fisnar commands, get an array of fisnar command bytes
        # assumes that whichever dipsenser(s) appear in the fisnar commands are
        # connected

        # TODO: passing continuous_extrusion as a parameter here is really ghetto. In the future,
        # this should be a member function and it should internally acess self.continuous_extrusion.
        # Also, doing this separately for continuous/non continuous printing is really ghetto. This
        # should be a short term fix. The main issue is that the non continuous loop makes assumptions
        # that don't hold for continuous printing

        ret_bytes = []
        i = 0

        if continuous_extrusion:  # might lead to shittier prints (ID() leads to delay in movement - similar issue that octoprint faces - consequence of asynchronous printing)
            while i < len(fisnar_commands):
                if fisnar_commands[i][0] == "Output":
                    ret_bytes.append(FisnarCommands.OU(fisnar_commands[i][1], fisnar_commands[i][2]))
                elif fisnar_commands[i][0] == "Line Speed":
                    ret_bytes.append(FisnarCommands.SP(fisnar_commands[i][1]))
                elif fisnar_commands[i][0] == "Dummy Point":
                    ret_bytes.append(FisnarCommands.VA(fisnar_commands[i][1], fisnar_commands[i][2], fisnar_commands[i][3]))
                    ret_bytes.append(FisnarCommands.ID())
                i += 1
            return ret_bytes
        else:
            while i < len(fisnar_commands):
                if fisnar_commands[i][0] == "Output" and fisnar_commands[i][2] == 1:
                    output = fisnar_commands[i][1]
                    i += 1
                    consecutive_dummies = 0
                    while i < len(fisnar_commands) and fisnar_commands[i][0] == "Dummy Point":
                        if consecutive_dummies >= 99:
                            ret_bytes.append(FisnarCommands.OU(output, 1))  # output on
                            ret_bytes.append(FisnarCommands.ID())
                            ret_bytes.append(FisnarCommands.OU(output, 0))  # output off
                            consecutive_dummies = 0

                        ret_bytes.append(FisnarCommands.VA(fisnar_commands[i][1], fisnar_commands[i][2], fisnar_commands[i][3]))
                        i += 1
                        consecutive_dummies += 1

                    line_speed = fisnar_commands[i][1]
                    i += 2  # skip the output command that comes afterward

                    ret_bytes.append(FisnarCommands.OU(output, 1))  # output on
                    ret_bytes.append(FisnarCommands.ID())
                    ret_bytes.append(FisnarCommands.OU(output, 0))  # output off
                    ret_bytes.append(FisnarCommands.SP(line_speed))
                else:
                    if fisnar_commands[i][0] == "Dummy Point":
                        ret_bytes.append(FisnarCommands.VA(fisnar_commands[i][1], fisnar_commands[i][2], fisnar_commands[i][3]))
                        ret_bytes.append(FisnarCommands.ID())
                        i += 1
                    elif fisnar_commands[i][0] == "Line Speed":
                        ret_bytes.append(FisnarCommands.SP(fisnar_commands[i][1]))
                        i += 1
                    else:
                        Logger.log("w", "unaccounted for command in fisnar_commands: " + str(fisnar_commands[i]))
                        i += 1
            return ret_bytes

    @staticmethod
    def readFisnarCommandsFromCSV(csv_string):
        # given a string in CSV format, return a 2d array of fisnar commands

        # get the csv cells into a 2D array (again, no error checking)
        commands = [line.split(",") for line in csv_string.split("\n")]

        # converting all 2D array entries into proper types
        i = 0
        while i < len(commands):
            if commands[i][0] == "Output":
                for j in range(1, 3):
                    commands[i][j] = int(commands[i][j])
            elif commands[i][0] == "Dummy Point":
                for j in range(1, 4):
                    commands[i][j] = float(commands[i][j])
            elif commands[i][0] == "Line Speed":
                commands[i][1] = float(commands[i][1])
            elif commands[i][0] == "Z Clearance":
                commands[i][1] = int(commands[i][1])
            elif commands[i][0] == "End Program":
                pass
            elif commands[i][0] in ("Line Start", "Line End", "Line Passing"):
                for j in range(1, 4):
                    commands[i][j] = float(commands[i][j])
            else:
                Logger.log("d", "Unexpected command: '" + str(commands[i][0]) + "'")  # for debugging
                commands.pop(i)
                i -= 1  # to be immediately cancelled out by the following line - stay at the same index
            i += 1

        return copy.deepcopy(commands)
