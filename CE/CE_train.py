import argparse
import torch
from torch import cuda
import torch.nn as nn
import random
import sys
sys.path.append('..')
import glob
from itertools import chain
import os


from CE_opts import MarkdownHelpAction, model_opts, train_opts
import onmt
import onmt.io
import onmt.Models
import onmt.ModelConstructor
import onmt.modules
from onmt.Utils import use_gpu


parser = argparse.ArgumentParser(description="CE_train", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('-md', action=MarkdownHelpAction, help='print Markdown-formatted help text and exit.')
model_opts(parser)
train_opts(parser)
opt = parser.parse_args()
if opt.word_vec_size != -1:
    opt.src_word_vec_size = opt.word_vec_size
    opt.tgt_word_vec_size = opt.word_vec_size

if opt.layers != -1:
    opt.enc_layers = opt.layers
    opt.dec_layers = opt.layers
opt.brnn = (opt.encoder_type == "brnn")

if opt.seed > 0:
    random.seed(opt.seed)
    torch.manual_seed(opt.seed)

if opt.rnn_type == "SRU" and not opt.gpuid:
    raise AssertionError("Using SRU requires -gpuid set.")

if torch.cuda.is_available() and not opt.gpuid:
    print("WARNING: You have a CUDA device, should run with -gpuid 0")

if opt.gpuid:
    cuda.set_device(opt.gpuid[0])
    if opt.seed > 0:
        torch.cuda.manual_seed(opt.seed)

if len(opt.gpuid) > 1:
    sys.stderr.write("Sorry, multigpu isn't supported yet, coming soon!\n")
    sys.exit(1)


# Set up the Crayon logging server.
if opt.exp_host != "":
    from pycrayon import CrayonClient
    cc = CrayonClient(hostname=opt.exp_host)

    experiments = cc.get_experiment_names()
    print(experiments)
    if opt.exp in experiments:
        cc.remove_experiment(opt.exp)
    experiment = cc.create_experiment(opt.exp)


def report_func(epoch, batch, num_batches,
                start_time, lr, report_stats, report_flag):
    """
    This is the user-defined batch-level traing progress
    report function.

    Args:
        epoch(int): current epoch count.
        batch(int): current batch count.
        num_batches(int): total number of batches.
        start_time(float): last report time.
        lr(float): current learning rate.
        report_stats(Statistics): old Statistics instance.
    Returns:
        report_stats(Statistics): updated Statistics instance.
    """
    # if batch % opt.report_every == -1 % opt.report_every:
    if report_flag:
        report_stats.output(epoch, batch+1, num_batches, start_time)
        if opt.exp_host:
            report_stats.log("progress", experiment, lr)
        report_stats = onmt.Statistics()

    return report_stats


class DatasetIter(object):
    """ An Ordered Dataset Iterator, supporting multiple datasets,
        and lazy loading.

    Args:
        datsets (list): a list of datasets, which are lazily loaded.
        fields (dict): fields dict for the datasets.
        batch_size (int): batch size.
        batch_size_fn: custom batch process function.
        device: the GPU device.
        is_train (bool): train or valid?
    """
    def __init__(self, datasets, fields, batch_size, batch_size_fn,
                 device, is_train):
        self.datasets = datasets
        self.fields = fields
        self.batch_size = batch_size
        self.batch_size_fn = batch_size_fn
        self.device = device
        self.is_train = is_train

        self.cur_iter = self._next_dataset_iterator(datasets)
        # We have at least one dataset.
        assert self.cur_iter is not None

    def __iter__(self):
        dataset_iter = (d for d in self.datasets)
        while self.cur_iter is not None:
            for batch in self.cur_iter:
                yield batch
            self.cur_iter = self._next_dataset_iterator(dataset_iter)

    def __len__(self):
        # We return the len of cur_dataset, otherwise we need to load
        # all datasets to determine the real len, which loses the benefit
        # of lazy loading.
        assert self.cur_iter is not None
        return len(self.cur_iter)

    def get_cur_dataset(self):
        return self.cur_dataset

    def _next_dataset_iterator(self, dataset_iter):
        try:
            self.cur_dataset = next(dataset_iter)
        except StopIteration:
            return None

        # We clear `fields` when saving, restore when loading.
        self.cur_dataset.fields = self.fields

        # Sort batch by decreasing lengths of sentence required by pytorch.
        # sort=False means "Use dataset's sortkey instead of iterator's".
        return onmt.io.OrderedIterator(
                dataset=self.cur_dataset, batch_size=self.batch_size,
                batch_size_fn=self.batch_size_fn,
                device=self.device, train=self.is_train,
                sort=False, sort_within_batch=True,
                repeat=False)


def load_dataset(corpus_type):
    """
    Dataset generator. Don't do extra stuff here, like printing,
    because they will be postponed to the first loading time.

    Args:
        corpus_type: 'train' or 'valid'
    Returns:
        A list of dataset, the dataset(s) are lazily loaded.
    """
    assert corpus_type in ["train", "valid"]

    def dataset_loader(pt_file, corpus_type):
        dataset = torch.load(pt_file)
        print('Loading %s dataset from %s, number of examples: %d' %
              (corpus_type, pt_file, len(dataset)))
        return dataset

    # Sort the glob output by file name (by increasing indexes).
    pts = sorted(glob.glob(opt.data + '.' + corpus_type + '.[0-9]*.pt'))
    if pts:
        for pt in pts:
            yield dataset_loader(pt, corpus_type)
    else:
        # Only one onmt.io.*Dataset, simple!
        pt = opt.data + '.' + corpus_type + '.pt'
        yield dataset_loader(pt, corpus_type)


def make_dataset_iter(datasets, fields, opt, is_train=True):
    """
    This returns user-defined train/validate data iterator for the trainer
    to iterate over during each train epoch. We implement simple
    ordered iterator strategy here, but more sophisticated strategy
    like curriculum learning is ok too.
    """
    batch_size = opt.batch_size if is_train else opt.valid_batch_size
    batch_size_fn = None
    if is_train and opt.batch_type == "tokens":
        def batch_size_fn(new, count, sofar):
            return sofar + max(len(new.tgt), len(new.src)) + 1

    device = opt.gpuid[0] if opt.gpuid else -1

    return DatasetIter(datasets, fields, batch_size, batch_size_fn,
                           device, is_train)


def make_loss_compute(model, tgt_vocab, opt):
    """
    This returns user-defined LossCompute object, which is used to
    compute loss in train/validate process. You can implement your
    own *LossCompute class, by subclassing LossComputeBase.
    """
    if opt.copy_attn:
        compute = onmt.modules.CopyGeneratorLossCompute(
            model.generator, tgt_vocab, opt.copy_attn_force)
    else:
        compute = onmt.Loss.NMTLossCompute(
            model.generator, tgt_vocab,
            label_smoothing=opt.label_smoothing)

    if use_gpu(opt):
        compute.cuda()

    return compute


def train_model(model, disc, nli, fields, g_optim, d_optim, nli_optim, data_type, model_opt):

    train_loss = make_loss_compute(model, fields["tgt"].vocab, opt)
    valid_loss = make_loss_compute(model, fields["tgt"].vocab, opt)

    trunc_size = opt.truncated_decoder  # Badly named...
    shard_size = opt.max_generator_batches

    trainer = onmt.Trainer(model, disc, nli, train_loss, valid_loss, g_optim, d_optim, nli_optim,
                           trunc_size, shard_size, data_type,
                           opt.normalization, opt.accum_count)

    for epoch in range(opt.start_epoch, opt.epochs + 1):
        print('')

        # 1. Train for one epoch on the training set.
        train_datasets = load_dataset("train")
        train_iter = make_dataset_iter(train_datasets, fields, opt)
        train_stats = trainer.train(train_iter, epoch, report_func)
        print('Train accuracy: %g' % (100 * float(int(train_stats.n_acc)/train_stats.n_batch)))

        # 2. Validate on the validation set.
        valid_iter = make_dataset_iter(load_dataset("valid"),
                                       fields, opt,
                                       is_train=False)

        valid_stats = trainer.validate(valid_iter)
        print('Validation accuracy: %g' % valid_stats.accuracy())

        # 3. Log to remote server.
        if opt.exp_host:
            train_stats.log("train", experiment, g_optim.lr)
            valid_stats.log("valid", experiment, g_optim.lr)

        # 4. Update the learning rate
        trainer.epoch_step(valid_stats.ppl(), epoch)

        # 5. Drop a checkpoint if needed.
        if epoch >= opt.start_checkpoint_at:
            trainer.drop_checkpoint(model_opt, epoch, fields, valid_stats)


def load_fields(dataset, data_type, checkpoint):

    fields = onmt.io.load_fields_from_vocab(
                torch.load(opt.data + '.vocab.pt'), data_type)
    fields = dict([(k, f) for (k, f) in fields.items()
                  if k in dataset.examples[0].__dict__])
    fields['per'].vocab = fields['tgt'].vocab

    # if checkpoint is not None:
    #     print('Loading vocab from checkpoint at %s.' % opt.train_from)
    #     fields = onmt.io.load_fields_from_vocab(
    #                 checkpoint['vocab'], data_type)

    if data_type == 'text':
        print(' * vocabulary size. source = %d; target = %d' %
              (len(fields['src'].vocab), len(fields['tgt'].vocab)))
    else:
        print(' * vocabulary size. target = %d' %
              (len(fields['tgt'].vocab)))

    return fields


def collect_report_features(fields):
    src_features = onmt.io.collect_features(fields, side='src')
    tgt_features = onmt.io.collect_features(fields, side='tgt')

    for j, feat in enumerate(src_features):
        print(' * src feature %d size = %d' % (j, len(fields[feat].vocab)))
    for j, feat in enumerate(tgt_features):
        print(' * tgt feature %d size = %d' % (j, len(fields[feat].vocab)))


def build_model(model_opt, opt, fields, checkpoint, d_checkpoint):
    print('Building model...')
    model, disc, nli = onmt.ModelConstructor.make_base_model(model_opt, fields,
                                                  use_gpu(opt), checkpoint, d_checkpoint)
    if len(opt.gpuid) > 1:
        print('Multi gpu training: ', opt.gpuid)
        model = nn.DataParallel(model, device_ids=opt.gpuid, dim=1)
    print(model)

    return model, disc, nli


def build_optim(model, checkpoint, type, optim, learning_rate):
    if opt.train_from and checkpoint and False:
        print('Loading optimizer from checkpoint.')
        optim = checkpoint[type]
        optim.optimizer.load_state_dict(
            checkpoint[type].optimizer.state_dict())
    else:
        optim = onmt.Optim(
            optim, learning_rate, opt.max_grad_norm,
            lr_decay=opt.learning_rate_decay,
            start_decay_at=opt.start_decay_at,
            beta1=opt.adam_beta1,
            beta2=opt.adam_beta2,
            adagrad_accum=opt.adagrad_accumulator_init,
            decay_method=opt.decay_method,
            warmup_steps=opt.warmup_steps,
            model_size=opt.rnn_size)

    optim.set_parameters(model.parameters())

    return optim


def check_save_model_path():
    save_model_path = os.path.abspath(opt.save_model)
    model_dirname = os.path.dirname(save_model_path)
    if not os.path.exists(model_dirname):
        os.makedirs(model_dirname)


def tally_parameters(model):
    n_params = sum([p.nelement() for p in model.parameters()])
    print('* number of parameters: %d' % n_params)
    enc = 0
    dec = 0
    for name, param in model.named_parameters():
        if 'encoder' in name:
            enc += param.nelement()
        elif 'decoder' or 'generator' in name:
            dec += param.nelement()
    print('encoder: ', enc)
    print('decoder: ', dec)



def main():
    print("Loading train/validate datasets from '%s'" % opt.data)
    train_datasets = load_dataset("train")
    print(' * maximum batch size: %d' % opt.batch_size)

    # Peek the fisrt dataset to determine the data_type.
    # (This will load the first dataset.)
    first_dataset = next(train_datasets)
    train_datasets = chain([first_dataset], train_datasets)
    data_type = first_dataset.data_type

    if opt.train_from:
        print('Loading checkpoint from %s' % opt.train_from)
        checkpoint = torch.load(opt.train_from,
                                map_location=lambda storage, loc: storage)
        d_checkpoint = None
        model_opt = checkpoint['opt']
        # I don't like reassigning attributes of opt: it's not clear.
        opt.start_epoch = checkpoint['epoch'] + 1
        if opt.d_train_from:
            d_checkpoint = torch.load(opt.d_train_from, map_location=lambda storage, loc: storage)
    else:
        checkpoint = None
        d_checkpoint = None
        model_opt = opt

    # Load fields generated from preprocess phase.
    fields = load_fields(first_dataset, data_type, checkpoint)

    # Report src/tgt features.
    collect_report_features(fields)

    # Build model.
    model, disc, nli = build_model(model_opt, opt, fields, checkpoint, d_checkpoint)
    tally_parameters(model)
    check_save_model_path()

    # Build optimizer.
    g_optim = build_optim(model, checkpoint, 'g_optim', opt.g_optim, opt.g_learning_rate)
    d_optim = build_optim(disc, checkpoint, 'd_optim', opt.d_optim, opt.d_learning_rate)
    nli_optim = build_optim(nli, checkpoint, 'nli_optim', opt.nli_optim, opt.nli_learning_rate)

    # Do training.
    train_model(model, disc, nli, fields, g_optim, d_optim, nli_optim, data_type, model_opt)


if __name__ == '__main__':
    main()
