"""Training script for sequence to sequence learning."""

import argparse
import sys
import random
import os
import shlex
from shutil import copyfile
import subprocess
import traceback
import numpy as np
import tensorflow as tf
from tensorflow.contrib.tensorboard.plugins import projector

from neuralmonkey.checking import (CheckingException, check_dataset_and_coders,
                                   check_unused_initializers)
from neuralmonkey.logging import Logging, log, debug
from neuralmonkey.config.configuration import Configuration
from neuralmonkey.learning_utils import training_loop
from neuralmonkey.dataset import Dataset
from neuralmonkey.model.sequence import EmbeddedFactorSequence
from neuralmonkey.tf_manager import get_default_tf_manager


def create_config() -> Configuration:
    config = Configuration()

    # training loop arguments
    config.add_argument("tf_manager", required=False, default=None)
    config.add_argument("epochs", cond=lambda x: x >= 0)
    config.add_argument("trainer")
    config.add_argument("batch_size", cond=lambda x: x > 0)
    config.add_argument("train_dataset")
    config.add_argument("val_dataset")
    config.add_argument("output")
    config.add_argument("evaluation")
    config.add_argument("runners")
    config.add_argument("test_datasets", required=False, default=[])
    config.add_argument("logging_period", required=False, default=20)
    config.add_argument("validation_period", required=False, default=500)
    config.add_argument("visualize_embeddings", required=False, default=None)
    config.add_argument("val_preview_input_series",
                        required=False, default=None)
    config.add_argument("val_preview_output_series",
                        required=False, default=None)
    config.add_argument("val_preview_num_examples",
                        required=False, default=15)
    config.add_argument("train_start_offset", required=False, default=0)
    config.add_argument("runners_batch_size", required=False, default=None)
    config.add_argument("postprocess")
    config.add_argument("name", required=False,
                        default="Neural Monkey Experiment")
    config.add_argument("random_seed", required=False)
    config.add_argument("initial_variables", required=False, default=None)
    config.add_argument("overwrite_output_dir", required=False, default=False)

    return config


def save_git_info(repo_dir: str, git_commit_file: str, git_diff_file: str,
                  branch: str = "HEAD"):
    with open(git_commit_file, "wb") as file:
        subprocess.run(["git", "log", "-1", "--format=%H", branch],
                       cwd=repo_dir, stdout=file)

    with open(git_diff_file, "wb") as file:
        subprocess.run(["git", "--no-pager", "diff", "--color=always", branch],
                       cwd=repo_dir, stdout=file)


# pylint: disable=too-many-statements, too-many-locals, too-many-branches
def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", metavar="INI-FILE",
                        help="the configuration file for the experiment")
    parser.add_argument("-s", "--set", type=str, metavar="SETTING",
                        action="append", dest="config_changes", default=[],
                        help="override an option in the configuration; the "
                        "syntax is [section.]option=value")
    parser.add_argument("-v", "--var", type=str, metavar="VAR", default=[],
                        action="append", dest="config_vars",
                        help="set a variable in the configuration; the syntax "
                        "is var=value (shorthand for -s vars.var=value)")
    parser.add_argument("-i", "--init", dest="init_only", action="store_true",
                        help="initialize the experiment directory and exit "
                        "without building the model")
    parser.add_argument("-f", "--overwrite", action="store_true",
                        help="force overwriting the output directory; can be "
                        "used to start an experiment created with --init")
    args = parser.parse_args()

    # define valid parameters and defaults
    cfg = create_config()
    # load the params from the config file, getting also the simple arguments
    args.config_changes.extend("vars.{}".format(s) for s in args.config_vars)
    cfg.load_file(args.config, changes=args.config_changes)
    # various things like randseed or summarywriter should be set up here
    # so that graph building can be recorded
    # build all the objects specified in the config

    if cfg.args.random_seed is None:
        cfg.args.random_seed = 2574600
    random.seed(cfg.args.random_seed)
    np.random.seed(cfg.args.random_seed)
    tf.set_random_seed(cfg.args.random_seed)

    # pylint: disable=no-member
    if (os.path.isdir(cfg.args.output) and
            os.path.exists(os.path.join(cfg.args.output, "experiment.ini"))):
        if cfg.args.overwrite_output_dir or args.overwrite:
            # we do not want to delete the directory contents
            log("Directory with experiment.ini '{}' exists, "
                "overwriting enabled, proceeding."
                .format(cfg.args.output))
        else:
            log("Directory with experiment.ini '{}' exists, "
                "overwriting disabled."
                .format(cfg.args.output), color="red")
            exit(1)

    # pylint: disable=broad-except
    if not os.path.isdir(cfg.args.output):
        try:
            os.mkdir(cfg.args.output)
        except Exception as exc:
            log("Failed to create experiment directory: {}. Exception: {}"
                .format(cfg.args.output, exc), color="red")
            exit(1)

    args_file = "{}/args".format(cfg.args.output)
    log_file = "{}/experiment.log".format(cfg.args.output)
    ini_file = "{}/experiment.ini".format(cfg.args.output)
    orig_ini_file = "{}/original.ini".format(cfg.args.output)
    git_commit_file = "{}/git_commit".format(cfg.args.output)
    git_diff_file = "{}/git_diff".format(cfg.args.output)
    variables_file_prefix = "{}/variables.data".format(cfg.args.output)

    cont_index = 0

    while (os.path.exists(log_file)
           or os.path.exists(ini_file)
           or os.path.exists(git_commit_file)
           or os.path.exists(git_diff_file)
           or os.path.exists(variables_file_prefix)
           or os.path.exists("{}.0".format(variables_file_prefix))):
        cont_index += 1

        args_file = "{}/args.cont-{}".format(
            cfg.args.output, cont_index)
        log_file = "{}/experiment.log.cont-{}".format(
            cfg.args.output, cont_index)
        ini_file = "{}/experiment.ini.cont-{}".format(
            cfg.args.output, cont_index)
        orig_ini_file = "{}/original.ini.cont-{}".format(
            cfg.args.output, cont_index)
        git_commit_file = "{}/git_commit.cont-{}".format(
            cfg.args.output, cont_index)
        git_diff_file = "{}/git_diff.cont-{}".format(
            cfg.args.output, cont_index)
        variables_file_prefix = "{}/variables.data.cont-{}".format(
            cfg.args.output, cont_index)

    with open(args_file, "w") as file:
        print(" ".join(shlex.quote(a) for a in sys.argv), file=file)

    cfg.save_file(ini_file)
    copyfile(args.config, orig_ini_file)

    if args.init_only:
        log("Experiment directory initialized.")

        cmd = [os.path.basename(sys.argv[0]), "-f", ini_file]
        log("To start experiment, run: {}".format(" ".join(shlex.quote(a)
                                                           for a in cmd)))
        exit(0)

    Logging.set_log_file(log_file)

    # this points inside the neuralmonkey/ dir inside the repo, but
    # it does not matter for git.
    repo_dir = os.path.dirname(os.path.realpath(__file__))
    save_git_info(repo_dir, git_commit_file, git_diff_file)

    cfg.build_model(warn_unused=True)

    tf_manager = cfg.model.tf_manager
    if cfg.model.tf_manager is None:
        tf_manager = get_default_tf_manager()

    tf_manager.init_saving(variables_file_prefix)

    try:
        check_dataset_and_coders(cfg.model.train_dataset,
                                 cfg.model.runners)
        if isinstance(cfg.model.val_dataset, Dataset):
            check_dataset_and_coders(cfg.model.val_dataset, cfg.model.runners)
        else:
            for val_dataset in cfg.model.val_dataset:
                check_dataset_and_coders(val_dataset, cfg.model.runners)

        check_unused_initializers()
    except CheckingException as exc:
        log(str(exc), color="red")
        exit(1)

    if cfg.model.visualize_embeddings:

        tb_projector = projector.ProjectorConfig()

        for sequence in cfg.model.visualize_embeddings:
            # TODO this check should be done when abstract class of embedded
            # sequences will be created, not only EmbeddedFactorSequence
            if not isinstance(sequence, EmbeddedFactorSequence):
                raise ValueError("Visualization must be embedded sequence.")
            sequence.tb_embedding_visualization(cfg.model.output, tb_projector)

        summary_writer = tf.summary.FileWriter(cfg.model.output)
        projector.visualize_embeddings(summary_writer, tb_projector)

    Logging.print_header(cfg.model.name, cfg.args.output)

    # runners_batch_size must be set to avoid problems on GPU
    if cfg.model.runners_batch_size is None:
        cfg.model.runners_batch_size = cfg.model.batch_size

    training_loop(
        tf_manager=tf_manager,
        epochs=cfg.model.epochs,
        trainer=cfg.model.trainer,
        batch_size=cfg.model.batch_size,
        log_directory=cfg.model.output,
        evaluators=cfg.model.evaluation,
        runners=cfg.model.runners,
        train_dataset=cfg.model.train_dataset,
        val_dataset=cfg.model.val_dataset,
        test_datasets=cfg.model.test_datasets,
        logging_period=cfg.model.logging_period,
        validation_period=cfg.model.validation_period,
        val_preview_input_series=cfg.model.val_preview_input_series,
        val_preview_output_series=cfg.model.val_preview_output_series,
        val_preview_num_examples=cfg.model.val_preview_num_examples,
        postprocess=cfg.model.postprocess,
        train_start_offset=cfg.model.train_start_offset,
        runners_batch_size=cfg.model.runners_batch_size,
        initial_variables=cfg.model.initial_variables)


def main() -> None:
    try:
        _main()
    except KeyboardInterrupt:
        log("Training interrupted by user.")
        debug(traceback.format_exc())
        exit(1)
