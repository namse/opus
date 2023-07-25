'''Copyright (c) 2017-2018 Mozilla
   Copyright (c) 2022 Amazon

   Redistribution and use in source and binary forms, with or without
   modification, are permitted provided that the following conditions
   are met:

   - Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.

   - Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

   THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
   ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
   LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
   A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE FOUNDATION OR
   CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
   EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
   PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
   PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
   LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
   NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
   SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
'''

import numpy as np

from .c_writer import CWriter

def print_vector(writer, vector, name, dtype='float', reshape_8x4=False, static=True, debug_float=False):

    f = writer.source
    binary_blob = writer.enable_binary_blob

    dtype_suffix = {
        'float' : 'float',
        'opus_int8' : 'int8',
        'int' : 'int',
        'qweight': 'qweight'
    }


    if binary_blob:
        f.write(
f'''
#ifndef USE_WEIGHTS_FILE
'''
        )
        writer.weight_arrays.add(name)

    if reshape_8x4:
        vector = vector.reshape((vector.shape[0]//4, 4, vector.shape[1]//8, 8))
        vector = vector.transpose((2, 0, 3, 1))

    v = np.reshape(vector, (-1))

    if debug_float:
        f.write('#ifndef DISABLE_DEBUG_FLOAT\n')

    f.write(
f'''
#define WEIGHTS_{name}_DEFINED
#define WEIGHTS_{name}_TYPE WEIGHT_TYPE_{dtype_suffix[dtype]}
'''
    )

    if static:
        f.write('static ')

    f.write(f'const {dtype} {name}[{len(v)}] = {{\n    ')

    for i in range(0, len(v)):

        f.write(f'{v[i]}')

        if (i!=len(v)-1):
            f.write(',')
        else:
            break

        if (i%8==7):
            f.write("\n    ")
        else:
            f.write(" ")

    f.write('\n};\n\n')
    if debug_float: f.write('#endif /*DISABLE_DEBUG_FLOAT*/\n')

    if binary_blob:
        f.write(
f'''
#endif /* USE_WEIGHTS_FILE */
'''
        )

    return vector



def extract_diagonal(A):
    """ input shape is (N, k*N) """

    N, M = A.shape
    B = A.copy()
    assert M % N == 0
    k = M // N

    diags = []
    for l in range(k):
        diag = np.diag(B[:, l * N : (l+1) * N]).copy()
        B[:, l * N : (l+1) * N] -= np.diag(diag)
        diags.append(diag)

    diag = np.concatenate(diags)

    return diag, B

def quantize_weight(weight, scale):
    Aq = np.round(weight / scale).astype('int')
    if Aq.max() > 127 or Aq.min() <= -128:
        raise ValueError("value out of bounds in quantize_weight")
    Aq = np.clip(np.round(weight / scale).astype('int'), -128, 127)
    return Aq


def print_sparse_weight(writer, A, name, scale=1/128, have_diag=True, quantize=False):
    N = A.shape[0]
    M = A.shape[1]
    W = np.zeros((0,), dtype='int')
    W0 = np.zeros((0,))

    if have_diag:
        diag, A = extract_diagonal(A)
        print_vector(writer, diag, name + '_diag')

    Aq = quantize_weight(A, scale)

    # extract blocks
    idx = np.zeros((0,), dtype='int')
    for i in range(M//8):
        pos = idx.shape[0]
        idx = np.append(idx, -1)
        nb_nonzero = 0
        for j in range(N//4):
            block = A[j*4:(j+1)*4, i*8:(i+1)*8]
            qblock = Aq[j*4:(j+1)*4, i*8:(i+1)*8]
            if np.sum(np.abs(block)) > 1e-10:
                nb_nonzero = nb_nonzero + 1
                idx = np.append(idx, j*4)
                vblock = qblock.transpose((1,0)).reshape((-1,))
                W0 = np.concatenate([W0, block.reshape((-1,))])
                W = np.concatenate([W, vblock])
        idx[pos] = nb_nonzero

    if quantize: print_vector(writer, W, name + '_int8', reshape_8x4=False, dtype='opus_int8')
    print_vector(writer, W0, name + '_float', reshape_8x4=False, dtype='float', debug_float=quantize)
    print_vector(writer, idx, name + '_idx', reshape_8x4=False, dtype='int')

    return Aq





def print_sparse_vector(writer, A, name, have_diag=True, quantize=False):
    N = A.shape[0]
    M = A.shape[1]
    W = np.zeros((0,), dtype='int')
    W0 = np.zeros((0,))
    if have_diag:
        diag = np.concatenate([np.diag(A[:,:N]), np.diag(A[:,N:2*N]), np.diag(A[:,2*N:])])
        A[:,:N] = A[:,:N] - np.diag(np.diag(A[:,:N]))
        A[:,N:2*N] = A[:,N:2*N] - np.diag(np.diag(A[:,N:2*N]))
        A[:,2*N:] = A[:,2*N:] - np.diag(np.diag(A[:,2*N:]))
        print_vector(writer, diag, name + '_diag')
    AQ = np.minimum(127, np.maximum(-128, np.round(A*128))).astype('int')
    idx = np.zeros((0,), dtype='int')
    for i in range(M//8):
        pos = idx.shape[0]
        idx = np.append(idx, -1)
        nb_nonzero = 0
        for j in range(N//4):
            block = A[j*4:(j+1)*4, i*8:(i+1)*8]
            qblock = AQ[j*4:(j+1)*4, i*8:(i+1)*8]
            if np.sum(np.abs(block)) > 1e-10:
                nb_nonzero = nb_nonzero + 1
                idx = np.append(idx, j*4)
                vblock = qblock.transpose((1,0)).reshape((-1,))
                W0 = np.concatenate([W0, block.reshape((-1,))])
                W = np.concatenate([W, vblock])
        idx[pos] = nb_nonzero

    if quantize: print_vector(writer, W, name + '_int8', reshape_8x4=False, dtype='opus_int8')
    print_vector(writer, W0, name + '_float', reshape_8x4=False, dtype='float', debug_float=quantize)
    print_vector(writer, idx, name + '_idx', reshape_8x4=False, dtype='int')

def print_sparse_vector(writer, A, name, have_diag=True, quantize=False):
    N = A.shape[0]
    M = A.shape[1]
    W = np.zeros((0,), dtype='int')
    W0 = np.zeros((0,))
    if have_diag:
        diag = np.concatenate([np.diag(A[:,:N]), np.diag(A[:,N:2*N]), np.diag(A[:,2*N:])])
        A[:,:N] = A[:,:N] - np.diag(np.diag(A[:,:N]))
        A[:,N:2*N] = A[:,N:2*N] - np.diag(np.diag(A[:,N:2*N]))
        A[:,2*N:] = A[:,2*N:] - np.diag(np.diag(A[:,2*N:]))
        print_vector(writer, diag, name + '_diag')
    AQ = np.minimum(127, np.maximum(-128, np.round(A*128))).astype('int')
    idx = np.zeros((0,), dtype='int')
    for i in range(M//8):
        pos = idx.shape[0]
        idx = np.append(idx, -1)
        nb_nonzero = 0
        for j in range(N//4):
            block = A[j*4:(j+1)*4, i*8:(i+1)*8]
            qblock = AQ[j*4:(j+1)*4, i*8:(i+1)*8]
            if np.sum(np.abs(block)) > 1e-10:
                nb_nonzero = nb_nonzero + 1
                idx = np.append(idx, j*4)
                vblock = qblock.transpose((1,0)).reshape((-1,))
                W0 = np.concatenate([W0, block.reshape((-1,))])
                W = np.concatenate([W, vblock])
        idx[pos] = nb_nonzero

    if quantize: print_vector(writer, W, name + '_int8', reshape_8x4=False, dtype='opus_int8')
    print_vector(writer, W0, name + '_float', reshape_8x4=False, dtype='float', debug_float=quantize)
    print_vector(writer, idx, name + '_idx', reshape_8x4=False, dtype='int')

    return AQ

def _check_activation(activation):
    if not activation in {"TANH", "SIGMOID", "LINEAR", "SWISH", "RELU", "SOFTMAX"}:
        raise ValueError(f"error: unknown activation {activation}")

def print_dense_layer(writer : CWriter,
                      name : str,
                      weight : np.ndarray,
                      bias : np.ndarray,
                      activation: str,
                      format : str = 'torch'):

    _check_activation(activation)

    if format == 'torch':
        weight = weight.transpose()

    print_vector(writer, weight, name + "_weights")
    print_vector(writer, bias, name + "_bias")

    writer.header.write(f"\n#define {name.upper()}_OUT_SIZE {weight.shape[1]}\n")

    if writer.enable_binary_blob:
        init_call = f'linear_init(&model->{name}, arrays, "{name}_bias", "{name}_weights", {weight.shape[0]}, {weight.shape[1]}, ACTIVATION_{activation})'
        writer.layer_dict[name] = ('DenseLayer', init_call)
    else:
        writer.source.write(
f"""

const DenseLayer {name} = {{
   {name}_bias,
   {name}_weights,
   {weight.shape[0]},
   {weight.shape[1]},
   ACTIVATION_{activation}
}};

"""
        )

        writer.header.write(f"\nextern const DenseLayer {name};\n\n")





def print_conv1d_layer(writer : CWriter,
                       name : str,
                       weight : np.ndarray,
                       bias : np.ndarray,
                       activation: str,
                       format : str = 'torch'):

    _check_activation(activation)

    if format == "torch":
        # convert to channels last
        weight = np.transpose(weight, (2, 1, 0))

    print_vector(writer, weight, name + "_weights")
    print_vector(writer, bias, name + "_bias")

    writer.header.write(f"\n#define {name.upper()}_OUT_SIZE {weight.shape[2]}\n")
    writer.header.write(f"\n#define {name.upper()}_STATE_SIZE ({weight.shape[1]} * ({weight.shape[0] - 1}))\n")
    writer.header.write(f"\n#define {name.upper()}_DELAY {(weight.shape[0] - 1) // 2}\n") # CAVE: delay is not a property of the conv layer

    if writer.enable_binary_blob:
        init_call = f'conv1d_init(&model->{name}, arrays, "{name}_bias", "{name}_weights", {weight.shape[1]}, {weight.shape[0]}, {weight.shape[2]}, ACTIVATION_{activation})'
        writer.layer_dict[name] = ('Conv1DLayer', init_call)
    else:

        writer.source.write(
f"""

const Conv1DLayer {name} = {{
   {name}_bias,
   {name}_weights,
   {weight.shape[1]},
   {weight.shape[0]},
   {weight.shape[2]},
   ACTIVATION_{activation}
}};

"""
        )

        writer.header.write(f"\nextern const Conv1DLayer {name};\n\n")

    return weight.shape[0] * weight.shape[1]


def qn(string):
    if string == "NULL": return string
    else: return '"' + string + '"'

def print_linear_layer(writer : CWriter,
                       name : str,
                       weight : np.ndarray,
                       bias : np.ndarray,
                       scale : np.ndarray = None,
                       format : str = 'torch',
                       sparse : bool = False,
                       diagonal : bool = False,
                       quantize : bool = True):

    """ prints linear layer

    Parameters:
    -----------
    name : str
        layer name
    weight: np.ndarray
    ...
    scale: np.ndarray or None
        If None auto scaling will be applied. Otherwise, output channels will be multiplied by scale (the usual broadcasting rules apply).


    """

    if len(weight.shape) != 2:
        raise ValueError('expecting 2-dim weight array in print_linear_layer')

    if format == 'torch':
        weight = weight.transpose()

    bias_name           = "NULL" if bias is None else name + "_bias"
    subias_name         = name + "_subias" if quantize else "NULL"
    scale_name          = name + "_scale" if quantize else "NULL"
    idx_name            = name + "_weights_idx" if sparse else "NULL"
    float_weight_name   = name + "_weights_float"
    int_weight_name     = name + "_weights_int8" if quantize else "NULL"
    diag_name           = name + "_weights_diag" if sparse and diagonal else "NULL"

    nb_inputs, nb_outputs = weight.shape

    if scale is None:
        raise ValueError("None scale case not implemented yet.")


    if sparse:
        weight_q = print_sparse_weight(writer, weight, name + "_weights", scale=scale, have_diag=diagonal, quantize=quantize)
    else:
        weight_q = quantize_weight(weight, scale)

        if quantize:
            print_vector(writer, weight_q, name + "_weights_int8", dtype='opus_int8', reshape_8x4=True)

        print_vector(writer, weight, name + "_weights_float", dtype='float', reshape_8x4=False, debug_float=quantize)

    if quantize:
        subias = (np.zeros(nb_outputs) if bias is None else bias) - np.sum(weight_q * scale, axis=0)
        print_vector(writer, subias, name + "_subias")

        final_scale = scale / 127 * np.ones(nb_outputs)
        print_vector(writer, final_scale, name + "_scale")

    if bias is not None:
        print_vector(writer, bias, name + "_bias")


    init_call = f'linear_init(&model->{name}, arrays, {qn(bias_name)}, {qn(subias_name)}, {qn(int_weight_name)},' \
        + f'{qn(float_weight_name)}, {qn(idx_name)}, {qn(diag_name)}, {qn(scale_name)}, {nb_inputs}, {nb_outputs})'

    writer.layer_dict[name] = ('LinearLayer', init_call)


def print_gru_layer2(writer : CWriter,
                    name : str,
                    weight : np.ndarray,
                    recurrent_weight : np.ndarray,
                    bias : np.ndarray,
                    recurrent_bias : np.ndarray,
                    format : str = 'torch',
                    quantize : bool = False,
                    input_sparse : bool = False,
                    recurrent_sparse : bool = False,
                    scale=1/128
                    ):

    if format == "torch":
        # change gate ordering from rzn to zrn

        N = weight.shape[0] // 3
        for x in [weight, recurrent_weight, bias, recurrent_bias]:
            tmp = x[0:N].copy()
            x[0:N] = x[N:2*N]
            x[N:2*N] = tmp

    print_linear_layer(writer, name + "_input", weight, bias, scale=scale, format=format, sparse=input_sparse, quantize=quantize)
    print_linear_layer(writer, name + "_recurrent", recurrent_weight, recurrent_bias, scale=scale, format=format, sparse=recurrent_sparse, diagonal=recurrent_sparse, quantize=quantize)

    # wrapping it up
    writer.header.write(f"\n#define {name.upper()}_OUT_SIZE {N}\n")
    writer.header.write(f"\n#define {name.upper()}_STATE_SIZE {N}\n")

def print_gru_layer(writer : CWriter,
                    name : str,
                    weight : np.ndarray,
                    recurrent_weight : np.ndarray,
                    bias : np.ndarray,
                    recurrent_bias : np.ndarray,
                    activation: str,
                    format : str = 'torch',
                    dotp : bool = False,
                    input_sparse : bool = False,
                    reset_after : int = 0
                    ):

    _check_activation(activation)

    if format == "torch":
        # transpose weight matrices and change gate order from rzn to zrn

        N = weight.shape[0] // 3
        for x in [weight, recurrent_weight, bias, recurrent_bias]:
            tmp = x[0:N].copy()
            x[0:N] = x[N:2*N]
            x[N:2*N] = tmp

        weight = weight.transpose()
        recurrent_weight = recurrent_weight.transpose()


    # input weights
    if input_sparse:
        qweight = print_sparse_vector(writer, weight, name + '_weights', have_diag=False)
    else:
        qweight = np.clip(np.round(128. * weight).astype('int'), -128, 127)

        if dotp:
            writer.source.write("#ifdef DOT_PROD\n")
            print_vector(writer, qweight, name + '_weights', dtype='qweight', dotp=True)
            writer.source.write("#else /*DOT_PROD*/\n")

        print_vector(writer, weight, name + '_weights')

        if dotp:
             writer.source.write("#endif /*DOT_PROD*/\n")


    # recurrent weights
    recurrent_qweight = np.clip(np.round(128. * recurrent_weight).astype('int'), -128, 127)

    if dotp:
        writer.source.write("#ifdef DOT_PROD\n")
        print_vector(writer, recurrent_qweight, name + '_recurrent_weights', dtype='qweight', dotp=True)
        writer.source.write("#else /*DOT_PROD*/\n")

    print_vector(writer, recurrent_weight, name + '_recurrent_weights')

    if dotp:
        writer.source.write("#endif /*DOT_PROD*/\n")


    # corrected bias for unsigned int matrix multiplication
    subias              = bias - np.sum(qweight / 128., axis=0)
    recurrent_subias    = recurrent_bias - np.sum(recurrent_qweight / 128., axis=0)

    print_vector(writer, np.concatenate((bias, recurrent_bias)), name + "_bias")
    print_vector(writer, np.concatenate((subias, recurrent_subias)), name + "_subias")


    # wrapping it up
    writer.header.write(f"\n#define {name.upper()}_OUT_SIZE {N}\n")
    writer.header.write(f"\n#define {name.upper()}_STATE_SIZE {N}\n")

    if writer.enable_binary_blob:
        if input_sparse:
            init_call = f'gru_init(&model->{name}, arrays, "{name}_bias", "{name}_subias", "{name}_weights", "{name + "_weights_idx"}", "{name}_recurrent_weights", {weight.shape[0]}, {weight.shape[1] // 3}, ACTIVATION_{activation}, {reset_after})'
        else:
            init_call = f'gru_init(&model->{name}, arrays, "{name}_bias", "{name}_subias", "{name}_weights", NULL, "{name}_recurrent_weights", {weight.shape[0]}, {weight.shape[1] // 3}, ACTIVATION_{activation}, {reset_after})'

        writer.layer_dict[name] = ('GRULayer', init_call)

    else:

        writer.source.write(
f"""

const GRULayer {name} = {{
   {name}_bias,
   {name}_subias,
   {name}_weights,
   {name + "_weights_idx" if input_sparse else "NULL"},
   {name}_recurrent_weights,
   {weight.shape[0]},
   {weight.shape[1] // 3},
   ACTIVATION_{activation},
   {reset_after}
}};

"""
        )

        writer.header.write(f"\nextern const GRULayer {name};\n")


    return N
