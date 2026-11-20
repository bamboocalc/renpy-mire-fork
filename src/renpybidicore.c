#include <Python.h>

#include <fribidi/fribidi.h>

#include <stdlib.h>

/* Buffers live on the heap; alloca overflowed the stack on long strings. */

PyObject *renpybidi_log2vis(PyObject *s, int *direction) {
    Py_ssize_t size;
    FriBidiChar *srcuni;
    FriBidiChar *dstuni;
    PyObject *rv;

    PyUnicode_READY(s);
    size = PyUnicode_GET_LENGTH(s);

    srcuni = (FriBidiChar *) PyMem_Malloc(size * sizeof(FriBidiChar));
    dstuni = (FriBidiChar *) PyMem_Malloc(size * sizeof(FriBidiChar));

    if (!srcuni || !dstuni) {
        PyMem_Free(srcuni);
        PyMem_Free(dstuni);
        return PyErr_NoMemory();
    }

    PyUnicode_AsUCS4(s, (Py_UCS4 *) srcuni, size, 0);

    fribidi_log2vis(
        srcuni,
        size,
        (FriBidiParType *) direction,
        dstuni,
        NULL,
        NULL,
        NULL);

    rv = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, dstuni, size);

    PyMem_Free(srcuni);
    PyMem_Free(dstuni);

    return rv;
}

PyObject *renpybidi_get_embedding_levels(PyObject *s, int *direction) {
    Py_ssize_t size;
    FriBidiChar *srcuni;
    FriBidiCharType *types;
    FriBidiLevel *levels;
    PyObject *rv = NULL;
    FriBidiLevel ret;

    PyUnicode_READY(s);
    size = PyUnicode_GET_LENGTH(s);

    srcuni = (FriBidiChar *) PyMem_Malloc(size * sizeof(FriBidiChar));
    types = (FriBidiCharType *) PyMem_Malloc(size * sizeof(FriBidiCharType));
    levels = (FriBidiLevel *) PyMem_Malloc(size * sizeof(FriBidiLevel));

    if (!srcuni || !types || !levels) {
        PyErr_NoMemory();
        goto done;
    }

    PyUnicode_AsUCS4(s, (Py_UCS4 *) srcuni, size, 0);

    fribidi_get_bidi_types(
        srcuni,
        size,
        types);

    ret = fribidi_get_par_embedding_levels_ex(
        types,
        NULL,
        size,
        (FriBidiParType *) direction,
        levels);

    if (ret == 0) {
        Py_INCREF(Py_None);
        rv = Py_None;
        goto done;
    }

    rv = PyTuple_New(size);

    for (int i=0; rv && i<size; i++)
        PyTuple_SetItem(rv, i, Py_BuildValue("i", (int) levels[i]));

done:
    PyMem_Free(srcuni);
    PyMem_Free(types);
    PyMem_Free(levels);

    return rv;
}
