"""
  This script provides an exmaple to wrap UER-py for ChID (a multiple choice dataset) inference.
"""
import sys
import os
import torch
import json
import random
import argparse
import collections
import numpy as np
import torch.nn as nn

uer_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(uer_dir)

from uer.utils.vocab import Vocab
from uer.utils.constants import *
from uer.utils.tokenizer import *
from uer.utils.config import load_hyperparam
from uer.model_loader import load_model
from run_classifier import batch_loader
from run_c3 import MultipleChoice
from run_chid import read_dataset


def postprocess_chid_predictions(results):
    index2tag = {index: tag for index, (tag, logits) in enumerate(results)}
    logits_matrix = [logits for _, logits in results]

    logits_matrix = np.transpose(np.array(logits_matrix))
    logits_matrix_list = []
    for i, row in enumerate(logits_matrix):
        for j, value in enumerate(row):
            logits_matrix_list.append((i, j, value))
    else:
        choices = set(range(i + 1))
        blanks = set(range(j + 1))
    logits_matrix_list = sorted(logits_matrix_list, key=lambda x: x[2], reverse=True)
    results = []
    for i, j, v in logits_matrix_list:
        if (j in blanks) and (i in choices):
            results.append((i, j))
            blanks.remove(j)
            choices.remove(i)
    results = sorted(results, key=lambda x: x[1], reverse=False)
    results = [[index2tag[j], i] for i, j in results]
    return results


def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    # Path options.
    parser.add_argument("--load_model_path", default=None, type=str,
                        help="Path of the multiple choice model.")
    parser.add_argument("--vocab_path", type=str, required=True,
                        help="Path of the vocabulary file.")
    parser.add_argument("--spm_model_path", default=None, type=str,
                        help="Path of the sentence piece model.")
    parser.add_argument("--test_path", type=str,
                        help="Path of the testset.")
    parser.add_argument("--prediction_path", default=None, type=str,
                        help="Path of the prediction file.")
    parser.add_argument("--config_path", default="./models/bert_base_config.json", type=str,
                        help="Path of the config file.")

    # Model options.
    parser.add_argument("--batch_size", type=int, default=32,
                        help="Batch size.")
    parser.add_argument("--seq_length", type=int, default=64,
                        help="Sequence length.")
    parser.add_argument("--max_choices_num", default=10, type=int,
                        help="The maximum number of cadicate answer, shorter than this will be padded.")
    parser.add_argument("--embedding", choices=["bert", "word"], default="bert",
                        help="Emebdding type.")
    parser.add_argument("--encoder", choices=["bert", "lstm", "gru", \
                                              "cnn", "gatedcnn", "attn", "synt", \
                                              "rcnn", "crnn", "gpt", "bilstm"], \
                                              default="bert", help="Encoder type.")
    parser.add_argument("--bidirectional", action="store_true", help="Specific to recurrent model.")
    parser.add_argument("--factorized_embedding_parameterization", action="store_true", help="Factorized embedding parameterization.")
    parser.add_argument("--parameter_sharing", action="store_true", help="Parameter sharing.")

    # Tokenizer options.
    parser.add_argument("--tokenizer", choices=["bert", "char", "space"], default="char",
                        help="Specify the tokenizer."
                             "Original Google BERT uses bert tokenizer on Chinese corpus."
                             "Char tokenizer segments sentences into characters."
                             "Space tokenizer segments sentences into words according to space."
                        )

    args = parser.parse_args()

    # Load the hyperparameters from the config file.
    args = load_hyperparam(args)

    # Build tokenizer.
    args.tokenizer = globals()[args.tokenizer.capitalize() + "Tokenizer"](args)

    # Build classification model and load parameters.
    model = MultipleChoice(args)
    model = load_model(model, args.load_model_path)

    # For simplicity, we use DataParallel wrapper to use multiple GPUs.
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    if torch.cuda.device_count() > 1:
        print("{} GPUs are available. Let's use them.".format(torch.cuda.device_count()))
        model = torch.nn.DataParallel(model)

    dataset = read_dataset(args, args.test_path, None)

    model.eval()
    batch_size = args.batch_size
    results_final = []
    dataset_by_group = {}
    print("The number of prediction instances: ", len(dataset))

    for example in dataset:
        if example[-1] not in dataset_by_group:
            dataset_by_group[example[-1]] = [example]
        else:
            dataset_by_group[example[-1]].append(example)

    for group_index, examples in dataset_by_group.items():
        src = torch.LongTensor([example[0] for example in examples])
        tgt = torch.LongTensor([example[1] for example in examples])
        seg = torch.LongTensor([example[2] for example in examples])
        index = 0
        results = []
        for i, (src_batch, _, seg_batch, _) in enumerate(batch_loader(batch_size, src, tgt, seg)):

            src_batch = src_batch.to(device)
            seg_batch = seg_batch.to(device)

            with torch.no_grad():
                _, logits = model(src_batch, None, seg_batch)
                pred = torch.argmax(logits, dim=1)
                pred = pred.cpu().numpy().tolist()
                for j in range(len(pred)):
                    results.append((examples[index][-2], logits[index].cpu().numpy()))
                    index += 1
        results_final.extend(postprocess_chid_predictions(results))

    with open(args.prediction_path, 'w') as f:
        json.dump({tag: pred for tag, pred in results_final}, f, indent=2)


if __name__ == "__main__":
    main()