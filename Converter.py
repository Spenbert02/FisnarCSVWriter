from .gcodeBuddy.marlin import Command
from UM.Logger import Logger


class Converter:
    # class that facilitates the conversion of gcode commands into Fisnar
    # commands


    # enumeration for different conversion modes
    IO_CARD = 2
    NO_IO_CARD = 3

    # movement commands
    XYZ_COMMANDS = ("Dummy Point", "Line Start", "Line Passing", "Line End")

    def __init__(self):
        self.gcode_commands_str = None  # gcode commands as string separated by \n characters
        self.gcode_commands_lst = None  # gcode commands as a list of Command objects
        self.last_converted_fisnar_commands = None  # the last converted fisnar command list

        # fisnar print surface coordinates
        self.fisnar_x_min = None
        self.fisnar_x_max = None
        self.fisnar_y_min = None
        self.fisnar_y_max = None
        self.fisnar_z_max = None

        # extruder-output correlations
        self.extruder_1_output = None
        self.extruder_2_output = None
        self.extruder_3_output = None
        self.extruder_4_output = None

        self.conversion_mode = Converter.IO_CARD  # default to using io card

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


    def setPrintSurface(self, x_min, x_max, y_min, y_max, z_max):
        # set the Fisnar print surface coordinates
        self.fisnar_x_min = x_min
        self.fisnar_x_max = x_max
        self.fisnar_y_min = y_min
        self.fisnar_y_max = y_max
        self.fisnar_z_max = z_max


    def getPrintSurface(self):
        # get the Fisnar print surface area as an array of coords in the form:
        # [x min, x max, y min, y max, z max]
        return [self.fisnar_x_min,
                self.fisnar_x_max,
                self.fisnar_y_min,
                self.fisnar_y_max,
                self.fisnar_z_max]


    def setExtruderOutputs(self, ext_1, ext_2, ext_3, ext_4):
        # set the output assigned to each extruder
        self.extruder_1_output = ext_1
        self.extruder_2_output = ext_2
        self.extruder_3_output = ext_3
        self.extruder_4_output = ext_4


    def getExtruderOutputs(self):
        # get the outputs associated with each extruder in a list of the form:
        # [extr 1, extr 2, extr 3, extr 4]
        return [self.extruder_1_output,
                self.extruder_2_output,
                self.extruder_3_output,
                self.extruder_4_output]


    def setGcode(self, gcode_str):
        # sets the gcode string (and gcode list)
        self.gcode_commands_str = gcode_str
        self.gcode_commands_lst = Converter.getStrippedCommands(gcode_str.split("\n"))


    def getExtrudersInGcode(self):
        # get the extruders used in the gcode file in a list of bools [extruder 1, extruder 2, extruder3, extruder 4]

        ret_extruders = [False, False, False, False]
        for command in self.gcode_commands_lst:
            if command.get_command()[0] == "T":
                ret_extruders[int(command.get_command()[1])] = True

        # if no toolhead commands are given, there must only be one extruder
        if ret_extruders == [False, False, False, False]:
            Logger.log("i", "Only one extruder is being used")
            ret_extruders[0] = True

        return ret_extruders  # extruder 1 is T0, extruder 2 is T1, etc etc so the max T is one less than the number of extruders


    def getFisnarCommands(self):
        # get the fisnar command list from the last set gcode commands and settings.
        # returns False if an error occurs, and sets its information to an error description

        # ensuring gcode commands exist
        if self.gcode_commands_lst is None:
            self.setInformation("internal error: in getFisnarCommands(), gcode_commands_lst is None")
            return False

        # gcode and user extruder lists
        gcode_extruders = self.getExtrudersInGcode()  # list of bools
        user_extruders = self.getExtruderOutputs()  # list of ints

        if self.conversion_mode == Converter.NO_IO_CARD:  # io card is not being used
            if gcode_extruders != [True, False, False, False]:  # if wrong extruders for no io
                self.setInformation("multiple extruders are used in gcode - i/o card must be used")
                return False
        elif self.conversion_mode == Converter.IO_CARD:  # io card is being used
            # checking that user has entered necessary extruder outputs
            for i in range(len(gcode_extruders)):
                if gcode_extruders[i]:
                    if user_extruders[i] is None:  # user hasn't entered output for extruder 'i + 1'
                        self.setInformation("Output for extruder " + str(i + 1) + " must be entered")
                        return False
        else:
            self.setInformation("internal error: no conversion mode set for Converter (Converter.getFisnarCommands)")
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
        extruder_outputs = self.getExtruderOutputs()

        # in case there isn't enough commands (this should never happen in slicer output)
        if first_relevant_command_index is None or last_relevant_command_index is None:
            self.setInformation("not enough gcode commands to deduce Fisnar commands")
            return False

        # default fisnar initial commands
        fisnar_commands = [["Line Speed", 30], ["Z Clearance", 5], ["SET ME AFTER CONVERTING COORD SYSTEM"]]

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

            # considering the command for io card conversion
            if self.conversion_mode == Converter.IO_CARD:
                if first_relevant_command_index <= i <= last_relevant_command_index:  # command needs to be converted
                    if command.get_command() in ("G0", "G1"):
                        new_commands = Converter.g0g1WithIO(command, extruder_outputs[curr_extruder], curr_pos)
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

            # considering the command for non io card conversion
            elif self.conversion_mode == Converter.NO_IO_CARD:
                if first_relevant_command_index <= i < last_relevant_command_index:  # the last command will be 'manually' converted after this loop
                    if command.get_command() in ("G0", "G1"):
                        j = i + 1
                        while self.gcode_commands_lst[j].get_command() not in ("G0", "G1"):
                            j += 1
                        next_extruding_command = self.gcode_commands_lst[j]
                        new_command = Converter.g0g1NoIO(command, next_extruding_command, curr_pos)
                        fisnar_commands.append(new_command)
                    elif command.get_command() in ("G2", "G3"):
                        pass
                    elif command.get_command() == "G90":
                        pass
                    elif command.get_command() == "G91":
                        pass
                elif i == last_relevant_command_index:  # last command (necessarily a line end)
                    if command.has_param("X"):
                        curr_pos[0] = command.get_param("X")
                    if command.has_param("Y"):
                        curr_pos[1] = command.get_param("Y")
                    if command.has_param("Z"):
                        curr_pos[2] = command.get_param("Z")
                    fisnar_commands.append(["Line End", curr_pos[0], curr_pos[1], curr_pos[2]])

            else:
                self.setInformation("internal error: no conversion mode set for Converter (Converter.convertCommands)")
                return False


        # turning off all outputs
        if self.conversion_mode == Converter.IO_CARD:
            for i in range(4):
                fisnar_commands.append(["Output", i + 1, 0])
        fisnar_commands.append(["End Program"])

        # inverting and shifting coordinate system from gcode to fisnar, then puttin home travel command
        Converter.invertCoords(fisnar_commands, self.fisnar_z_max)

        # this is effectively a homing command
        fisnar_commands[2] = ["Dummy Point", self.fisnar_x_min, self.fisnar_y_min, self.fisnar_z_max]

        # removing redundant output commands
        if self.conversion_mode == Converter.IO_CARD:
            Converter.optimizeFisnarOutputCommands(fisnar_commands)

        return fisnar_commands



    def boundaryCheck(self, fisnar_commands):
        # check that all coordinates are within the user specified area. If ANY
        # coordinates fall outside the volume, False will be returned - if all
        # coordinates fall within the volume, True will be returned

        print_surface = self.getPrintSurface()
        for command in fisnar_commands:
            if command[0] in Converter.XYZ_COMMANDS:
                if not (print_surface[0] <= command[1] <= print_surface[1]):
                    Logger.log("e", str(command))
                    return False
                if not (print_surface[2] <= command[2] <= print_surface[3]):
                    Logger.log("e", str(command))
                    return False
                if not (0 <= command[3] <= print_surface[4]):
                    Logger.log("e", str(command))
                    return False

        return True  # functioned hasn't returned False, so all good


    @staticmethod
    def g0g1NoIO(command, next_command, curr_pos):
        # take a command, the command after it, and the position before the
        # current command

        # determining command type
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

            if output_state is None:
                Logger.log("d", "output " + str(output) + " does not appear in Fisnar commands.")


    @staticmethod
    def invertCoords(commands, z_dim):
        # invert all coordinate directions for dummy points (modifies the given list)

        for i in range(len(commands)):
            if commands[i][0] in Converter.XYZ_COMMANDS:
                commands[i][1] = 200 - commands[i][1]
                commands[i][2] = 200 - commands[i][2]
                commands[i][3] = z_dim - commands[i][3]


if __name__ == "__main__":
    pass