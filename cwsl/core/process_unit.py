"""
Authors: Tim Bedin, Ricardo Pascual, Tim Erwin

Copyright 2014 CSIRO

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

This module contains the ProcessUnit class.

"""

import os
import logging

from cwsl.configuration import configuration
from cwsl.utils import utils
from cwsl.core.argument_creator import ArgumentCreator
from cwsl.core.file_creator import FileCreator
from cwsl.core.constraint import Constraint
from cwsl.core.scheduler import SimpleExecManager


module_logger = logging.getLogger('cwsl.core.process_unit')


class ProcessUnit(object):
    """ This class sets up the execution of an operation performed on input DataSets.

    This class takes in a list of input DataSets, an output
    pattern to write files to, the shell command that needs to be
    run and any output_constraints that need to be added to the
    output.

    It sets up the ArgumentCreator classes required to
    run a particular, single processing operation for all of
    the possible combinations in the input and output DataSets.

    This could be done using a VisTrails module directly, but doing it like
    this allows us to separate the GUI presentation from the execution.

    """

    def __init__(self, inputlist, output_pattern, shell_command,
                 extra_constraints=None, map_dict=None, cons_keywords=None,
                 positional_args=None, execution_options=None):

        """ 
        Arguments:
        
        inputlist: A list of input DataSets to get files from.

        output_pattern: A filename pattern to use for data output.

        shell_command: The base shell command for the process to run.

        Optional:
        
        extra_constraints: Extra constraints to be applied to the output.

        map_dict: a dictionary linking constraint names in the input DataSets
                  to new constraints in the output. e.g.
                  if map_dict = {"obs-model": ("model", 0)} then the "model" constraint in the
                  input position 0 is renamed to be the "obs-model" Constraint in the output.
        
        cons_keywords: Used in building the command to be run, if a constraint has to
                       be used as a keyword argument.

        positional_args: Used in building the command to be run, if a constraint
                         has to be used as a positional argument.

        execution_options: A dictionary to pass options like required queues, walltime,
                           required modules etc. to the process unit. Currently only
                           required_modules is implemented.

        """

        self.inputlist = inputlist
        self.shell_command = shell_command
        self.execution_options = execution_options
        
        self.cons_keywords = cons_keywords
        self.positional_args = positional_args
                
        # The initial Constraints for the output are built from the
        # output file pattern.
        pattern_constraints = set(FileCreator.constraints_from_pattern(output_pattern))

        # Apply mappings to copy the required Constraints from the input that will be used
        # in the output to the final collection of output constraints.
        mapped_constraints = self.apply_mappings(pattern_constraints,
                                                 map_dict)

        # Apply extra constraints given in constructor.
        filled_constraints = self.fill_constraints_from_extras(self.mapped_constraints,
                                                               extra_constraints)

        # Finallly fill the empty output constraints from the input DataSets.
        self.final_constraints = self.fill_from_input(self.inputlist, filled_constraints)
        module_logger.debug("Final output constraints are: {0}".format(self.final_constraints))

        # Make a file_creator from the new, fixed constraints.
        self.file_creator = FileCreator(output_pattern, self.final_constraints)

    def apply_mappings(self, input_cons, map_dict):
        """ Apply the mapping dictionary to constraints.

        This method applies the required mappings of input constraint to
        output constraint for all the input datasets.

        """

        if map_dict is None:
            return input_cons
        
        module_logger.debug("Before applying mappings, constraints for the output are: {}"
                            .format(input_cons))

        to_remove = []
        for map_name, map_spec in map_dict.items():
            
            # First update the outputs with values from the input.
            found_con = self.inputlist[map_spec[1]].get_constraint(map_spec[0])
            
            input_cons.add(Constraint(map_name, found_con.values))
            input_cons.remove(Constraint(map_name, []))

            self.inputlist[map_spec[1]].add_mapping(map_name, map_spec[0])

        
        module_logger.debug("After applying mappings, output_constraints are: {}"
                            .format(input_cons))

        return input_cons

    def fill_from_input(self):

        module_logger.debug("Before filling from input, output_constraints are: {}"
                            .format(self.output_constraints))

        new_cons = set([])
        to_remove = []
        for cons in self.output_constraints:
            if not cons.values:
                module_logger.debug("Trying to fill constraint: {}"
                                    .format(cons))
                found_cons = set([input_ds.get_constraint(cons.key)
                                  for input_ds in self.inputlist
                                  if input_ds.get_constraint(cons.key)])
                module_logger.debug("Found constraints: {}"
                                    .format(found_cons))
                new_cons = new_cons.union(found_cons)
                to_remove.append(cons)

        for cons in to_remove:
            self.output_constraints.remove(cons)

        self.output_constraints = self.output_constraints.union(new_cons)

        module_logger.debug("After filling from input, output_constraints are: {}"
                            .format(self.output_constraints))

    def fill_constraints_from_extras(self, output_constraints,
                                     extra_constraints):
        """ Fills the constraints generated from the file name
        
        Returns a set of constraints fill with extras
        given when constructing the class.
        """

        module_logger.debug("Before filling from extras, output constraints: {}"
                            .format(self.output_constraints))

        module_logger.debug("Extra constraints to fill are: {}"
                            .format(extra_constraints))

        # Make sure we are not overwriting with empty constraints.
        for cons in extra_constraints:
            if not cons.values:
                raise EmptyOverwriteError("Constraint {} is being used to overwrite"
                                          .format(cons))

        # Find the empty constraints to fill.
        empty_cons_names = [cons.key for cons in output_constraints
                            if not cons.values]
        module_logger.debug("Attempting to fill: {}"
                            .format(empty_cons_names))

        # Lists to hold constraints to add or remove.
        to_add = []
        to_remove = []

        # Add the extra_constraints if they are found in the output.
        for cons in extra_constraints:
            if cons.key in empty_cons_names:
                to_add.append(cons)
                # Remove the empty.
                to_remove += [bad_cons for bad_cons in output_constraints
                              if bad_cons.key == cons.key]

        for cons in to_remove:
            output_constraints.remove(cons)
        for cons in to_add:
            output_constraints.add(cons)

        module_logger.debug("After filling from extras, output constraints: {}"
                            .format(output_constraints))

        return output_constraints

    def execute(self, simulate=False):
        """ This method runs the actual process.

        This method returns a FileCreator to be used
        as input to the next VisTrails module.

        """

        # Check that cws_ctools_path is set
        if not configuration.cwsl_ctools_path or not os.path.exists(configuration.cwsl_ctools_path):
            raise Exception("cwsl_ctools_path is not set in package options")

        # We now create a looper to compare all the input Datasets with
        # the output fileCreators.
        this_looper = ArgumentCreator(self.inputlist, self.file_creator)
        module_logger.debug("Created ArgumentCreator: {0}".format(this_looper))

        #TODO determine scheduler from user options.
        scheduler = SimpleExecManager(noexec=simulate)
        if self.execution_options.has_key('required_modules'):
            scheduler.add_module_deps(self.execution_options['required_modules'])

        #Add environment variables to the script and the current environment.
        scheduler.add_environment_variables({'CWSL_CTOOLS':configuration.cwsl_ctools_path})
        os.environ['CWSL_CTOOLS'] = configuration.cwsl_ctools_path
        scheduler.add_python_paths([os.path.join(configuration.cwsl_ctools_path,'pythonlib')])

        # For every valid possible combination, apply any positional and
        # keyword args, then add the command to the scheduler.
        for combination in this_looper:
            module_logger.debug("Combination: " + str(combination))
            if combination:
                in_files, out_files = self.get_fullnames((combination[0], combination[1]))
                this_dict = combination[2]

                module_logger.info("in_files are:")
                module_logger.info(in_files)
                module_logger.info("out_files are:")
                module_logger.info(out_files)

                base_cmd_list = [self.shell_command] + in_files + out_files

                # Now apply any keyword arguments and positional args.
                keyword_command_list = self.apply_keyword_args(base_cmd_list, this_dict)
                final_command_list = self.apply_positional_args(keyword_command_list, this_dict)

                # Generate the annotation string.
                try:
                    annotation = utils.build_metadata(final_command_list)
                except NameError:
                    annotation = None

                # The subprocess / queue submission is done here.
                scheduler.add_cmd(final_command_list, out_files, annotation=annotation)

        scheduler.submit()

        # The scheduler is kept for testing purposes.
        self.scheduler = scheduler

        return self.file_creator

    def apply_keyword_args(self, command_list, kw_cons_dict, prefix='--'):
        """ Add keywords from the keyword constraint dictionary to the command list."""

        for keyword in self.cons_keywords:
            associated_cons_name = self.cons_keywords[keyword]
            this_att_value = kw_cons_dict[associated_cons_name]

            command_list.append(prefix + keyword + ' ' + this_att_value)

        return command_list

    def apply_positional_args(self, arg_list, constraint_dict):
        """ Add positional args to a list of command arguments. """

        for arg_tuple in self.positional_args:
            arg_name = arg_tuple[0]
            position = arg_tuple[1]

            try:
                if arg_tuple[2] == 'raw':
                    this_att_value = arg_name
            except IndexError:
                this_att_value = constraint_dict[arg_name]

            if position != -1:
                position += 1   # +1 because arg_list[0] is the actual command!
                arg_list.insert(position, this_att_value)
            else:
                arg_list.append(this_att_value)

        return arg_list

    def get_fullnames(self, combination):

        in_files = []
        for qs in combination[0]:
            in_files += [infile.full_path for infile in qs]

        out_files = []
        for qs in combination[1]:
            out_files += [outfile.full_path for outfile in qs]

        return in_files, out_files


# Exception Classes
class EmptyOverwriteError(Exception):
    pass
