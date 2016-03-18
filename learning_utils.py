from nltk.translate.bleu_score import corpus_bleu, SmoothingFunction
from termcolor import colored
import time

def log(message, color='yellow'):
    print "{}: {}".format(colored(time.strftime("%Y-%m-%d %H:%M:%S"), color), message)

def print_args(args):
    print ""
    for arg in vars(args):
        value = str(getattr(args, arg))
        dots_count = 78 - len(arg) - len(value)
        print "{} {} {}".format(arg, "".join(['.' for _ in range(dots_count)]), value)
    print ""

def print_title(title):
    print colored("".join("=" for _ in range(80)), 'green')
    print colored(title.upper, 'green')
    print colored("".join("=" for _ in range(80)), 'green')

def training_loop(sess, vocabulary, epochs, optimize_op,
                  decoder, train_feed_dicts, train_tgt_sentences,
                  val_feed_dict, val_tgt_sentences):
    """

    Performs the training loop for given graph and data.

    Args:

        sess: TF Session.

        vocabulary: Vocabulary used on the decoder side.

        epochs: Number of epochs for which the algoritm will learn.

        optimize_op: The optimization oepration.

        decoder: The decoder object.

        train_feed_dicts: List of feed dictionaires for training batches.

        train_tgt_sentences: List of batches of target training
            sentences.

        val_feed_dict: Feed dictionaty for the validation data.

        val_tgt_sentences: Validation target sentences. Lists of lists (there
            may be multiple references for a sentece) of list of words.


    """

    log("Starting training")
    step = 0
    bleu_smoothing = SmoothingFunction(epsilon=0.01).method1
    for i in range(epochs):
        print ""
        log("Epoch {} starts".format(i + 1), color='red')

        for batch_n, (batch_feed_dict, batch_sentences) in \
                enumerate(zip(train_feed_dicts, train_tgt_sentences)):
            step += 1
            if step % 20 == 1:
                computation = sess.run([optimize_op, decoder.loss_with_decoded_ins, decoder.loss_with_gt_ins] \
                        + decoder.decoded_seq, feed_dict=batch_feed_dict)
                decoded_sentences = \
                    vocabulary.vectors_to_sentences(computation[-decoder.max_output_len - 1:])

                bleu_1 = \
                    100 * corpus_bleu(batch_sentences, decoded_sentences, weights=[1., 0., 0., 0.],
                                      smoothing_function=bleu_smoothing)
                bleu_4 = \
                    100 * corpus_bleu(batch_sentences, decoded_sentences, weights=[0.25, 0.25, 0.25, 0.25],
                                      smoothing_function=bleu_smoothing)

                log("opt. loss: {:.4f}    dec. loss: {:.4f}    BLEU-1: {:.2f}    BLEU-4: {:.2f}"\
                        .format(computation[2], computation[1], bleu_1, bleu_4))
            else:
                sess.run([optimize_op], feed_dict=batch_feed_dict)

            if step % 50 == 49:
                computation = sess.run([decoder.loss_with_decoded_ins, decoder.loss_with_gt_ins] \
                        + decoder.decoded_seq, feed_dict=val_feed_dict)
                decoded_val_sentences = \
                    vocabulary.vectors_to_sentences(computation[-decoder.max_output_len - 1:])

                val_bleu_1 = \
                        100 * corpus_bleu(val_tgt_sentences, decoded_val_sentences, weights=[1., 0., 0., 0.0],
                                          smoothing_function=bleu_smoothing)
                val_bleu_4 = \
                    100 * corpus_bleu(val_tgt_sentences, decoded_val_sentences, weights=[0.25, 0.25, 0.25, 0.25],
                                      smoothing_function=bleu_smoothing)
                print ""
                log("Validation (epoch {}, batch number {}):".format(i, batch_n), color='cyan')
                log("opt. loss: {:.4f}    dec. loss: {:.4f}    BLEU-1: {:.2f}    BLEU-4: {:.2f}"\
                        .format(computation[1], computation[0], val_bleu_1, val_bleu_4), color='cyan')

                print ""
                print "Examples:"
                for sent, ref_sent in zip(decoded_val_sentences[:15], val_tgt_sentences):
                    print "    {}".format(" ".join(sent))
                    print colored("      ref.: {}".format(" ".join(ref_sent[0])), color="magenta")
                print ""

