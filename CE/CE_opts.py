import argparse
import sys
sys.path.append('..')

from onmt.modules.SRU import CheckSRU


class MarkdownHelpFormatter(argparse.HelpFormatter):
    """A really bare-bones argparse help formatter that generates valid markdown.
    This will generate something like:
    usage
    # **section heading**:
    ## **--argument-one**
    ```
    argument-one help text
    ```
    """

    def _format_usage(self, usage, actions, groups, prefix):
        return ""

    def format_help(self):
        print(self._prog)
        self._root_section.heading = '# Options: %s' % self._prog
        return super(MarkdownHelpFormatter, self).format_help()

    def start_section(self, heading):
        super(MarkdownHelpFormatter, self)\
            .start_section('### **%s**' % heading)

    def _format_action(self, action):
        if action.dest == "help" or action.dest == "md":
            return ""
        lines = []
        lines.append('* **-%s %s** ' % (action.dest,
                                        "[%s]" % action.default
                                        if action.default else "[]"))
        if action.help:
            help_text = self._expand_help(action)
            lines.extend(self._split_lines(help_text, 80))
        lines.extend(['', ''])
        return '\n'.join(lines)


class MarkdownHelpAction(argparse.Action):
    def __init__(self, option_strings,
                 dest=argparse.SUPPRESS, default=argparse.SUPPRESS,
                 **kwargs):
        super(MarkdownHelpAction, self).__init__(
            option_strings=option_strings,
            dest=dest,
            default=default,
            nargs=0,
            **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        parser.formatter_class = MarkdownHelpFormatter
        parser.print_help()
        parser.exit()


class DeprecateAction(argparse.Action):
    def __init__(self, option_strings, dest, help=None, **kwargs):
        super(DeprecateAction, self).__init__(option_strings, dest, nargs=0,
                                              help=help, **kwargs)

    def __call__(self, parser, namespace, values, flag_name):
        help = self.help if self.help is not None else ""
        msg = "Flag '%s' is deprecated. %s" % (flag_name, help)
        raise argparse.ArgumentTypeError(msg)


def model_opts(parser):
    group = parser.add_argument_group('Model-Embeddings')
    group.add_argument('-src_word_vec_size', type=int, default=300, help="Word embedding size for src.")
    group.add_argument('-tgt_word_vec_size', type=int, default=500, help="Word embedding size for tgt.")
    group.add_argument('-word_vec_size', type=int, default=-1, help="Word embedding size for src and tgt.")
    group.add_argument('-share_decoder_embeddings', action='store_true',
                       help="Use a shared weight matrix for the input and output word  embeddings in the decoder.")
    group.add_argument('-share_embeddings', action='store_true',
                       help="Share the word embeddings between encoder and decoder. "
                            "Need to use shared dictionary for this option.")
    group.add_argument('-position_encoding', action='store_true',
                       help="Use a sin to mark relative words positions. Necessary for non-RNN style models.")

    group = parser.add_argument_group('Model-Embedding Features')
    group.add_argument('-feat_merge', type=str, default='concat', choices=['concat', 'sum', 'mlp'],
                       help="Merge action for incorporating features embeddings. Options [concat|sum|mlp].")
    group.add_argument('-feat_vec_size', type=int, default=-1,
                       help="If specified, feature embedding sizes will be set to this. "
                            "Otherwise, feat_vec_exponent will be used.")
    group.add_argument('-feat_vec_exponent', type=float, default=0.7,
                       help="If -feat_merge_size is not set, feature embedding sizes will "
                            "be set to N^feat_vec_exponent where N is the number of values the feature takes.")

    group = parser.add_argument_group('Model-Encoder-Decoder')
    group.add_argument('-model_type', default='text',
                       help="Type of source model to use. Allows the system to incorporate non-text inputs. "
                            "Options are [text|img|audio].")
    group.add_argument('-encoder_type', type=str, default='rnn', choices=['rnn', 'brnn', 'mean', 'transformer', 'cnn'],
                       help="Type of encoder layer to use. Non-RNN layers are experimental. "
                            "Options are [rnn|brnn|mean|transformer|cnn].")
    group.add_argument('-decoder_type', type=str, default='rnn', choices=['rnn', 'transformer', 'cnn'],
                       help="Type of decoder layer to use. Non-RNN layers are experimental. "
                            "Options are [rnn|transformer|cnn].")
    group.add_argument('-layers', type=int, default=-1, help='Number of layers in enc/dec.')
    group.add_argument('-enc_layers', type=int, default=2, help='Number of layers in the encoder')
    group.add_argument('-dec_layers', type=int, default=2, help='Number of layers in the decoder')
    group.add_argument('-rnn_size', type=int, default=500, help='Size of rnn hidden states')
    group.add_argument('-cnn_kernel_width', type=int, default=3,
                       help="Size of windows in the cnn, the kernel_size is (cnn_kernel_width, 1) in conv layer")
    group.add_argument('-input_feed', type=int, default=1,
                       help="Feed the context vector at each time step "
                            "as additional input (via concatenation with the word embeddings) to the decoder.")
    group.add_argument('-rnn_type', type=str, default='LSTM', choices=['LSTM', 'GRU', 'SRU'], action=CheckSRU,
                       help="The gate type to use in the RNNs")
    group.add_argument('-brnn', action=DeprecateAction,
                       help="Deprecated, use `encoder_type`.")
    group.add_argument('-brnn_merge', default='concat', choices=['concat', 'sum'],
                       help="Merge action for the bidir hidden states")

    group.add_argument('-context_gate', type=str, default=None, choices=['source', 'target', 'both'],
                       help="Type of context gate to use. Do not select for no context gate.")

    # Attention options
    group = parser.add_argument_group('Model-Attention')
    group.add_argument('-global_attention', type=str, default='general', choices=['dot', 'general', 'mlp'],
                       help="""The attention type to use: dotprod or general (Luong) or MLP (Bahdanau)""")

    # Genenerator and loss options.
    group.add_argument('-copy_attn', action="store_true",
                       help='Train copy attention layer.')
    group.add_argument('-copy_attn_force', action="store_true",
                       help='When available, train to copy.')
    group.add_argument('-coverage_attn', action="store_true",
                       help='Train a coverage attention layer.')
    group.add_argument('-lambda_coverage', type=float, default=1,
                       help='Lambda value for coverage.')


def train_opts(parser):
    # Model loading/saving options

    group = parser.add_argument_group('General')
    group.add_argument('-data', default='../data/nli_persona',
                       help="""Path prefix to the ".train.pt" and
                       ".valid.pt" file path from preprocess.py""")

    group.add_argument('-save_model', default='model',
                       help="""Model filename (the model will be saved as
                       <save_model>_epochN_PPL.pt where PPL is the
                       validation perplexity""")
    # GPU
    group.add_argument('-gpuid', default=[0], nargs='+', type=int,
                       help="Use CUDA on the listed devices.")

    group.add_argument('-seed', type=int, default=-1,
                       help="""Random seed used for the experiments
                       reproducibility.""")

    # Init options
    group = parser.add_argument_group('Initialization')
    group.add_argument('-start_epoch', type=int, default=1,
                       help='The epoch from which to start')
    group.add_argument('-param_init', type=float, default=0.1,
                       help="""Parameters are initialized over uniform distribution
                       with support (-param_init, param_init).
                       Use 0 to not use initialization""")
    group.add_argument('-train_from', default='', type=str,
                       help="""If training from a checkpoint then this is the
                       path to the pretrained model's state_dict.""")
    group.add_argument('-d_train_from', default='', type=str,
                       help="""If training from a checkpoint then this is the
                           path to the pretrained model's state_dict.""")

    # Pretrained word vectors
    group.add_argument('-pre_word_vecs_enc',
                       help="""If a valid path is specified, then this will load
                       pretrained word embeddings on the encoder side.
                       See README for specific formatting instructions.""")
    group.add_argument('-pre_word_vecs_dec',
                       help="""If a valid path is specified, then this will load
                       pretrained word embeddings on the decoder side.
                       See README for specific formatting instructions.""")
    # Fixed word vectors
    group.add_argument('-fix_word_vecs_enc',
                       action='store_true',
                       help="Fix word embeddings on the encoder side.")
    group.add_argument('-fix_word_vecs_dec',
                       action='store_true',
                       help="Fix word embeddings on the encoder side.")

    # Optimization options
    group = parser.add_argument_group('Optimization- Type')
    group.add_argument('-batch_size', type=int, default=64,
                       help='Maximum batch size for training')
    group.add_argument('-batch_type', default='sents',
                       choices=["sents", "tokens"],
                       help="""Batch grouping for batch_size. Standard
                               is sents. Tokens will do dynamic batching""")
    group.add_argument('-normalization', default='sents',
                       choices=["sents", "tokens"],
                       help='Normalization method of the gradient.')
    group.add_argument('-accum_count', type=int, default=1,
                       help="""Accumulate gradient this many times.
                       Approximately equivalent to updating
                       batch_size * accum_count batches at once.
                       Recommended for Transformer.""")
    group.add_argument('-valid_batch_size', type=int, default=32,
                       help='Maximum batch size for validation')
    group.add_argument('-max_generator_batches', type=int, default=32,
                       help="""Maximum batches of words in a sequence to run
                        the generator on in parallel. Higher is faster, but
                        uses more memory.""")
    group.add_argument('-epochs', type=int, default=13,
                       help='Number of training epochs')
    group.add_argument('-g_optim', default='adam',
                       choices=['sgd', 'adagrad', 'adadelta', 'adam'],
                       help="""Optimization method.""")
    group.add_argument('-d_optim', default='adam',
                       choices=['sgd', 'adagrad', 'adadelta', 'adam'],
                       help="""Optimization method.""")
    group.add_argument('-nli_optim', default='adam',
                       choices=['sgd', 'adagrad', 'adadelta', 'adam'],
                       help="""Optimization method.""")
    group.add_argument('-adagrad_accumulator_init', type=float, default=0,
                       help="""Initializes the accumulator values in adagrad.
                       Mirrors the initial_accumulator_value option
                       in the tensorflow adagrad (use 0.1 for their default).
                       """)
    group.add_argument('-max_grad_norm', type=float, default=5,
                       help="""If the norm of the gradient vector exceeds this,
                       renormalize it to have the norm equal to
                       max_grad_norm""")
    group.add_argument('-dropout', type=float, default=0.3,
                       help="Dropout probability; applied in LSTM stacks.")
    group.add_argument('-truncated_decoder', type=int, default=0,
                       help="""Truncated bptt.""")
    group.add_argument('-adam_beta1', type=float, default=0.9,
                       help="""The beta1 parameter used by Adam.
                       Almost without exception a value of 0.9 is used in
                       the literature, seemingly giving good results,
                       so we would discourage changing this value from
                       the default without due consideration.""")
    group.add_argument('-adam_beta2', type=float, default=0.999,
                       help="""The beta2 parameter used by Adam.
                       Typically a value of 0.999 is recommended, as this is
                       the value suggested by the original paper describing
                       Adam, and is also the value adopted in other frameworks
                       such as Tensorflow and Kerras, i.e. see:
                       https://www.tensorflow.org/api_docs/python/tf/train/AdamOptimizer
                       https://keras.io/optimizers/ .
                       Whereas recently the paper "Attention is All You Need"
                       suggested a value of 0.98 for beta2, this parameter may
                       not work well for normal models / default
                       baselines.""")
    group.add_argument('-label_smoothing', type=float, default=0.0,
                       help="""Label smoothing value epsilon.
                       Probabilities of all non-true labels
                       will be smoothed by epsilon / (vocab_size - 1).
                       Set to zero to turn off label smoothing.
                       For more detailed information, see:
                       https://arxiv.org/abs/1512.00567""")
    # learning rate
    group = parser.add_argument_group('Optimization- Rate')
    group.add_argument('-g_learning_rate', type=float, default=0.001,
                       help="""Starting learning rate.
                       Recommended settings: sgd = 1, adagrad = 0.1,
                       adadelta = 1, adam = 0.001""")
    group.add_argument('-d_learning_rate', type=float, default=0.001,
                       help="""Starting learning rate.
                           Recommended settings: sgd = 1, adagrad = 0.1,
                           adadelta = 1, adam = 0.001""")
    group.add_argument('-nli_learning_rate', type=float, default=0.001,
                       help="""Starting learning rate.
                               Recommended settings: sgd = 1, adagrad = 0.1,
                               adadelta = 1, adam = 0.001""")
    group.add_argument('-learning_rate_decay', type=float, default=0.5,
                       help="""If update_learning_rate, decay learning rate by
                       this much if (i) perplexity does not decrease on the
                       validation set or (ii) epoch has gone past
                       start_decay_at""")
    group.add_argument('-start_decay_at', type=int, default=8,
                       help="""Start decaying every epoch after and including this
                       epoch""")
    group.add_argument('-start_checkpoint_at', type=int, default=0,
                       help="""Start checkpointing every epoch after and including
                       this epoch""")
    group.add_argument('-decay_method', type=str, default="",
                       choices=['noam'], help="Use a custom decay rate.")
    group.add_argument('-warmup_steps', type=int, default=4000,
                       help="""Number of warmup steps for custom decay.""")

    group = parser.add_argument_group('Logging')
    group.add_argument('-report_every', type=int, default=50,
                       help="Print stats at this interval.")
    group.add_argument('-exp_host', type=str, default="",
                       help="Send logs to this crayon server.")
    group.add_argument('-exp', type=str, default="",
                       help="Name of the experiment for logging.")

    group = parser.add_argument_group('Speech')
    # Options most relevant to speech
    group.add_argument('-sample_rate', type=int, default=16000,
                       help="Sample rate.")
    group.add_argument('-window_size', type=float, default=.02,
                       help="Window size for spectrogram in seconds.")



