.. _model:

Model-Based Rendering
=====================

While Ren'Py is primarily used with two dimensional rectangular images that
are common in visual novels, underneath the hood it has a model-based renderer
intended to take advantage of features found in modern GPUs. This allows
for a number of visual effects that would not otherwise be possible.

As a warning, this is one of the most advanced features available in Ren'Py.
In many cases, it's not necessary to understand how model-based rendering
works behind the scenes - features like :tpref:`matrixcolor` and Live2D support
can be used without understanding how Model-Based rendering works, and more
such features will be added to the understanding. This documentation is
intended for very advanced creators, and for developers looking to add
to Ren'Py itself.

Model-Based Rendering is one of the most advanced features in Ren'Py, and
this documentation may be hard to understand without first looking at the
OpenGL, OpenGL ES, GLSL, and GLSL ES manual. What's more, since there are
portions of the models that are passed directly to your GPU drivers, which
may accept erroneous inputs, it's important to check on multiple kinds of
hardware.


Models, Renders, and Drawing Operations
---------------------------------------

The fundamental thing that Ren'Py draws to the screen is a Model. A model
consists of the following things:

* A Mesh of one or more triangles. A triangle consists of
  three vertices (corners), each of which contains a position in two or
  three-dimensional space, and may contain additional information, most
  commonly texture coordinates.

* Zero or more textures, with the precise number allowed being limited by the
  GPUs your game can run on. All GPUs should support at least three textures
  per model. A texture is a rectangle containing image data that's been loaded
  on the GPU, either directly or using a render-to-texture operation.

* A list of shader part names. Ren'Py uses these shader parts to create shaders,
  which are programs that are run on the GPU to render the model. Shader part
  names can be prefixed with a "-" to prevent that shader part from being used.

* Uniform values. A uniform is additional data that is the same throughout the
  model. For example, when a model represents a solid color, the color is a
  uniform.

* GL properties. GL properties are flags that further control how things
  are rendered, such as the minification/magnification modes and the
  color mask.

As Ren'Py usually draws more than one thing to the screen, it creates a
tree of :class:`Render` objects. These Render objects may have Models or
other Renders as children. (A Render object can also be turned into a Model.
as described below.) A Render contains:

* A list of children, including a 2-dimensional offset that is applied to
  each child.

* A :class:`Matrix` that describes how the children are transformed in
  three-dimensional space.

* Lists of shader part names, uniforms, and GL properties that are applied
  to the Models when being drawn.

* Flags that determine if the drawable-space clipping polygon should be
  updated.

Ren'Py draws the screen by performing a depth-first walk through the tree of
Renders, until a Model is encountered. During this walk, Ren'Py updates a
matrix transforming the location of the Model, a clipping polygon, and
lists of shader parts, uniforms, and GL properties. When a Model is encountered
as part of this walk, the appropriate shader program is activated on the GPU,
all information is transferred, and a drawing operation occurs.


Where Models are Created
------------------------

Ren'Py creates Models automatically as part of its normal operation.
The main reason to understand where models are created is that models
correspond to drawing operations, and hence are the units that shaders
are applied to.

Images and Image Manipulators
    These create a model with a mesh containing two triangles that cover
    the rectangle of the image. The mesh contains texture coordinates.
    The model uses the "renpy.texture" shader.

:func:`Solid`
    The Solid displayable creates a mesh containing two triangles, and no
    texture coordinates. The model uses the "renpy.solid" shader,
    with the color placed in the ``u_renpy_solid_color`` uniform.

:func:`Dissolve`, :func:`ImageDissolve`, :func:`AlphaDissolve`, :func:`Pixellate`, :func:`AlphaMask`, :func:`Flatten`
    Each of these transforms and displayables creates a Model with a mesh,
    shaders, and uniforms as is needed for its purposes.

Live2D
    Live2D displayables may created multiple Models when rendered, generally
    one Model for each layer.

:func:`Transform` and ATL
    A Transform creates a model if :tpref:`mesh` is True, or if :tpref:`blur`
    is being used. In this case, the children of the Transform are rendered
    to textures, with the mesh of the first texture being used for the mesh
    associated with the model.

    Not every transform creates a Model. Some transforms will simply add
    shaders and uniforms to a Render (such as transforms that use
    :tpref:`blur` or :tpref:`alpha`). Other transforms simply affect
    geometry.

:class:`Render`
    A Transform creates a model if its ``mesh`` attribute is True.
    In this case, the children of the Render are rendered to
    textures, with the mesh of the first texture being used for
    the mesh associated with the model.

It's expected that Ren'Py will add more ways of creating models in the
future.

Shader Program Generation
-------------------------

Ren'Py generates a shader program by first assembling a list of shader part
names. This list consists of "renpy.geometry", the list of shader parts
taken from Renders, and the list of shader parts found in the Model being
drawn.

The shader parts are then deduplicated. If a shader part begins with "-",
it is removed from the list, as is the rest of that part without the
leading "-". (So "-renpy.geometry" will cause itself and "renpy.geometry"
to be removed.)

Ren'Py then takes the list of shader parts, and retrieves lists of variables,
functions, vertex shader parts, and fragment shader parts. These are, in turn,
used to generate the source code for shaders, with the parts of the vertex and
fragment shaders being included in low-number to high-number priority order.

This means that any variable created by one of the shaders will be accessible
by every other fragment from any other shader in the list of shader parts.
There is no scope like in Python functions to protect interference between
shaders.

Ren'Py keeps a cache of all combinations of shader parts that have ever been
used in game/cache/shaders.txt, and loads them at startup. If major changes
in shader use occur, this file should be edited or deleted so it can be
re-created with valid data.

On systems that support OpenGL program binaries, Ren'Py can also cache the linked
program for each combination. This cache is specific to the graphics driver; if
shader parts or GLSL version change or if the driver rejects a cached program,
Ren'Py re-compiles the shader from source and replaces the cached program
automatically.

When :var:`config.predict_shaders` is true, shader combinations exposed by
predicted displayables are compiled during expensive idle prediction.

Dynamic systems that know the shader combination they will use can register it
with :func:`renpy.start_predict_shader`, and remove it with
:func:`renpy.stop_predict_shader`.


.. _custom-shaders:

Creating a Custom Shader
-------------------------

New shader parts can be created by calling the renpy.register_shader
function and supplying portions of GLSL shaders.

Generally, shader parts should be of the form "namespace.part", such as
"mygame.recolor" or "mylibrary.warp". Names beginning with "renpy." or
"live2d." are reserved for Ren'Py, as are names beginning with _.

.. include:: inc/register_shader

Ren'Py supports only the following variable types:

* float (a Python float)
* vec2 (a tuple of 2 floats)
* vec3 (a tuple of 3 floats)
* vec4 (a tuple of 4 floats)
* int (a Python integer)
* ivec2 (a tuple of 2 integers)
* ivec3 (a tuple of 3 integers)
* ivec4 (a tuple of 4 integers)
* bool (a Python boolean)
* bvec2 (a tuple of 2 booleans)
* bvec3 (a tuple of 3 booleans)
* bvec4 (a tuple of 4 booleans)
* type[n] (an array of [n] elements of [type] (any of the types above) (for instance, int[5] would be an array of 5 integers))
* mat2 (a :class:`Matrix`)
* mat3 (a :class:`Matrix`)
* mat4 (a :class:`Matrix`)
* sampler2D (a Displayabe, including strings that give Displayables, or a Render)

Uniform variables should begin with u\_, attributes with a\_, and varying
variables with v\_. Names starting with u_renpy\_, a_renpy, and v_renpy
are reserved, as are the standard variables given below.

As a general sketch for priority levels, priority 100 sets up geometry,
priority 200 determines the initial fragment color (``fragment_color``, or
``gl_FragColor`` when ``glsl=100`` is in use), and higher-numbered priorities
can apply effects to alter that color.

Here's an example of a custom shader part that applies a gradient across
each model it is used to render::

    init python:

        renpy.register_shader("example.gradient", variables="""
            uniform vec4 u_gradient_left;
            uniform vec4 u_gradient_right;
            uniform vec2 u_model_size;
            out float v_gradient_done;
            in vec4 a_position;
        """, vertex_300="""
            v_gradient_done = a_position.x / u_model_size.x;
        """, fragment_300="""
            float gradient_done = v_gradient_done;
            fragment_color *= mix(u_gradient_left, u_gradient_right, gradient_done);
        """)

The custom shader can then be applied using a transform::

    transform gradient:
        shader "example.gradient"
        u_gradient_left (1.0, 0.0, 0.0, 1.0)
        u_gradient_right (0.0, 0.0, 1.0, 1.0)

    show eileen happy at gradient

As stated before, the ``gradient_done`` variable from the example.gradient shader
will be accessible by any and all other shaders applied from the same list. This
can be useful when having optional parts in a given shader system, but it can also
lead to name collisions when using two independent shaders.

.. _glsl-version:

**GLSL Version.**
Shader parts are written in one of two versions: GLSL ES 3.00 is described
throughout this page.

In GLSL ES 3.00, variables are declared with ``uniform``, ``in`` for a value
that comes from the mesh, and ``out`` for a value the vertex shader passes to
the fragment shader. Textures are sampled with ``texture``, and a fragment
shader writes its color to ``fragment_color``.

.. var:: config.glsl_version = 300

    The version used by shader parts that don't pass `glsl` to
    :func:`renpy.register_shader`. This defaults to 300 in new games and to
    100 in games that declare compatibility with Ren'Py 8.5 or earlier.

A single part can override this by passing `glsl` to
:func:`renpy.register_shader`.

When :var:`config.glsl_version` is 300, a part that doesn't pass `glsl` but
uses a construct that only exists in GLSL ES 1.00 (``attribute``, ``varying``,
``gl_FragColor``, ``texture2D``, and so on) is treated as being written in
GLSL ES 1.00, and a note is written to log.txt. Passing ``glsl=100`` makes
this explicit and silences the note. A part that doesn't pass `glsl`, and
that uses a construct that is only available in a newer version than
:var:`config.glsl_version` selects, raises an exception.

Ren'Py translates between the two versions, so parts written in either can be
combined in the same shader, and a variable declared by two parts in different
versions is treated as the same variable. GLSL ES 3.00 is preferred and
recommended for new shader parts.

Ren'Py emits shaders as GLSL ES 3.00 on mobile and the web, where an OpenGL ES
3.0 context is required. On the desktop, it emits GLSL 3.30 where it can get an
OpenGL 3.3 context and GLSL 1.20 where it can't (e.g. macOS).

Ren'Py declares variables to have highp precision, which supports floating
point numbers in the range 2\ :sup:`-62` to 2\ :sup:`62`. Non-zero floating
point numbers should have a magnitude between 2\ :sup:`-62` and
2\ :sup:`62`. Integers are 32 bits wide in GLSL ES 3.00, while GLSL 1.20 only
promises 16 bits of integer precision. Actual hardware often supports a larger
range, but this isn't guaranteed.

There is a variable that can help in debugging custom shaders:

.. var:: config.log_gl_shaders = False

    If true, source code for the GLSL shader programs will be written to
    log.txt on start.


.. _shader-local-variables:

Shader Part Local Variables
---------------------------

Variables can be declared shader-local by using one of ``u__``, ``a__``,
``v__``, or ``l__`` as a prefix. When this is done, the double underscores
are filled in with the shader name with all dots replaced with underscores.
For example, if the shader name is ``example.gradient``, the prefix
``u__`` will be replaced with ``u_example_gradient_``.

The main use of this is with :doc:`text shaders <textshaders>`, where most
uniforms are shader-local. Also, local variables inside the shader should
be declared with ``l__``.


Transforms and Model-Based Rendering
------------------------------------

Model-Based rendering adds the following properties to ATL and :func:`Transform`:

.. transform-property:: mesh

    :type: None or True or tuple
    :default: None

    If not None, this Transform will be rendered as a model. This means:

    * A mesh will be created. If this is a 2-component tuple, it's taken
      as the number of points in the mesh, in the x and y directions. (Each
      dimension must be at least 2.) If True, the mesh is taken from the
      child.
    * The child of this transform will be rendered to a texture.
    * The renpy.texture shader will be added.

    By default, the texture used by the mesh will not be mipmapped. If
    mipmaps are desired, :tpref:`gl_mipmap` should be set to True.

.. transform-property:: mesh_pad

    :type: None or tuple
    :default: None

    If not None, this can either be a 2 or 4-component tuple. If mesh is
    True and this is given, this applies padding to the size of the textures
    applied to the textures used by the mesh. A 2-component tuple applies
    padding to the right and bottom, while a 4-component tuple applies
    padding to the left, top, right, and bottom.

    This can be used, in conjunction with the ``gl_pixel_perfect`` property,
    to render text into a mesh. In Ren'Py, text is rendered at the screen
    resolution, which might overflow the boundaries of the texture that
    will be applied to the mesh. Adding a few pixels of padding makes the
    texture bigger, which will display all pixels. For example::

        transform adjust_text:
            mesh True
            mesh_pad (10, 0)
            gl_pixel_perfect True
            shader "shaders.adjust_text"

    will ensure that the texture passed to the shader contains all of the pixels
    of the text.

.. transform-property:: shader

    :type: None or str or list of str
    :default: None

    If not None, a shader part name or list of shader part names that will be
    applied to this Render (if a Model is created) or the Models reached
    through this Render.

.. transform-property:: blend

    :type: None or str
    :default: None

    if not None, this should be a string. This string is looked up in
    :var:`config.gl_blend_func` to get the value for the gl_blend_func
    property. It's used to use alternate blend modes.

    The default blend modes this supports are "normal", "add", "multiply",
    "min", and "max".


In addition, uniforms that start with u\_ but not with u_renpy are made available
as Transform properties. GL properties are made available as transform
properties starting with gl\_. For example, the color_mask property is made
available as gl_color_mask.

Blend Functions
---------------

.. var:: config.gl_blend_func = { ... }

    A dictionary used to map a blend mode name to a blend function. The
    blend modes are supplied to the :ref:`gl_blend_func <gl-blend-func>` property, given below.

The default blend modes are::

    gl_blend_func["normal"] = (GL_FUNC_ADD, GL_ONE, GL_ONE_MINUS_SRC_ALPHA, GL_FUNC_ADD, GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
    gl_blend_func["add"] = (GL_FUNC_ADD, GL_ONE, GL_ONE, GL_FUNC_ADD, GL_ZERO, GL_ONE)
    gl_blend_func["multiply"] = (GL_FUNC_ADD, GL_DST_COLOR, GL_ONE_MINUS_SRC_ALPHA, GL_FUNC_ADD, GL_ZERO, GL_ONE)
    gl_blend_func["min"] = (GL_MIN, GL_ONE, GL_ONE, GL_MIN, GL_ONE, GL_ONE)
    gl_blend_func["max"] = (GL_MAX, GL_ONE, GL_ONE, GL_MAX, GL_ONE, GL_ONE)

As Ren'Py uses premultiplied alpha, the results of some of these may be counterintuitive when a pixel
is not opaque. In the GPU, the color (r, g, b, a) is represented as (r * a, g * a, b * a, a), and the blend function
uses these premultiplied colors. This may be a different result that you get for these blend modes in a paint program,
when what is drawn is not fully opaque.

.. _model-uniforms:

Float, Sample, and Vector Uniforms
-----------------------------------

The following uniforms are made available to all models.

``vec2 u_model_size``
    The width and height of the model, as supplied to Ren'Py. This
    is only available for 2D models that supply a size, and is (0, 0)
    for 3d models.

.. _u-lod-bias:

``float u_lod_bias``
    The level of detail bias to apply to texture lookups. This may be
    set in a Transform. The default value, taken from :var:`config.gl_lod_bias`
    and defaulting to -0.5, biases Ren'Py to always pick the next bigger
    level and scale it down.

``float u_time``
    The time of the frame. The epoch is undefined, so it's best to treat
    this as a number that increases by one second a second. The time is
    modulo 86400, so it will reset to 0.0 once a day.

``vec4 u_random``
    Four random numbers between 0.0 and 1.0 that are (with incredibly high
    likelyhood) different from frame to frame.

``vec4 u_viewport``
    This gives the current viewport being drawn into. u_viewport.xy is
    are the coordinates of the bottom-left corner of the viewport, relative
    to the bottom-left corner of the window. u_viewport.pq is the width
    and height of the viewport.

``vec2 u_virtual_size``

    This is the virtual size of the game (:var:`config.screen_width`, :var:`config.screen_height`).
    This can be used to convert from gl_Position to virtual coordinates using:

    .. code-block:: glsl

        v_position = u_virtual_size * vec2(gl_Position.x * .5 + .5, -gl_Position.y * .5 + .5)

``vec2 u_drawable_size``
    The size of the drawable are of the windows, in pixels, at the resolution
    the game is running at. For example, if a 1280x720 game is scaled up to
    1980x1080, this will be (1920, 1080).

``sampler2D tex0``, ``sampler2D tex1``, ``sampler2D tex2``
    If textures are available, the corresponding samplers are placed in
    this variable.

``vec2 res0``, ``vec2 res1``, ``vec2 res2``
    If textures are available, the size of the textures are placed in these
    variables. When the texture is loaded from disk, this is the size of the
    image file. After a render to texture, it's the number of drawable pixels
    the rendered texture covered.

If a sampler uniform is available, then suffixing it with ``__res`` will give a vec2
containing the underlying texture size. For example, ``u_markup__res`` will give the size of the
``u_markup`` texture.

If a sampler uniform is available, then suffixing it with ``__virtual_size`` will give a vec2
containing the size of the texture in virtual pixels. Unlike ``__res``, this doesn't depend on the
resolution the texture was rendered or loaded at, so it doesn't change when the window is scaled.
For example, ``u_markup__virtual_size`` will give the virtual size of the ``u_markup`` texture.

If a sampler uniform holds a texture rendered with :ref:`gl_depth_texture <gl-properties>`, then
suffixing it with ``__depth`` will give a sampler2D containing that render's depth buffer. The built-in
samplers take these suffixes too, so ``tex0__depth`` is the depth of ``tex0``.

If a vec4 uniform is available, then suffixing it with ``__premul`` will give a vec4
containing the premultiplied color. For example, ``u_color__premul`` will give a vec4
where the red, green, and blue channels are multiplied by the alpha channel.

Matrix Uniforms
----------------

The following uniforms are made available to all models. This assumes that only one transform with the
:tpref:`perspective` property is used to render a model. When multiple transforms with the :tpref:`perspective`
are used, the innermost transformation with perspective set defines the world and view spaces.

``mat4 u_projection``
    This is a matrix that transforms coordinates from view space to the OpenGL viewport.
    This is sent by Ren'Py, and is updated by transforms with the :tpref:`perspective` property
    to encapsulate the effects of that property.

``mat4 u_view``
    This is a matrix that transforms vertex coordinates from the world space to the view space. This
    defaults to the identity matrix, but can be set by transforms with the :tpref:`perspective` property,
    in which case the effects of positioning, rotation, and scaling are encapsulated in this matrix.

``mat4 u_model``
    This is a matrix that transforms vertex coordinates from the model space to the world space.

``mat4 u_projectionview``
    This matrix contains ``u_projection * u_view``. It exists to minimize the number of uniforms that need
    to be sent to the GPU, and the amount of work that needs to be done in the shader.

``mat4 u_transform``
    This is the same as ``u_projectionview * u_model``. It's the matrix that transforms vertex coordinates
    directly to the OpenGL viewport. It exists to minimize the number of uniforms that need to be sent to the GPU,
    the amount of work that needs to be done in the shader, and for compatibility with older versions of Ren'Py.

In addition to these methods, Ren'Py can synthesize matrices with certain functions applied when suffixes are
appended to the matrix

``__inverse``
    When appended to a matrix, this returns the inverse of the matrix. For example, ``u_projection__inverse``
    is the inverse of the projection matrix.

``__transpose``
    When appended to a matrix, this returns the transpose of the matrix. For example, ``u_view__transpose``
    is the transpose of the view matrix.

``__inverse_transpose``
    When appended to a matrix, this returns the inverse of the transpose of the matrix. For example,
    ``u_model__inverse_transpose`` is the inverse of the transpose of the model matrix. This is useful for
    transforming normals.

Attributes
----------

The following attribute is available to all models:

``vec4 a_position``
    The position of the vertex being rendered. This is in virtual pixels, relative to the upper
    left corner of the texture.

If textures are available, so is the following attribute:

``vec2 a_tex_coord``
    The coordinate that this vertex projects to inside the textures.

If normals are available, so is the following attribute:

``vec3 a_normal``
    The normal of the vertex being rendered.

If tangents are available, so are the following attributes:

``vec3 a_tangent``
    The tangent of the vertex being rendered.

``vec3 a_bitangent``
    The bitangent of the vertex being rendered.

.. _gl-properties:

GL Properties
-------------

GL properties change the global state of OpenGL, or the Model-Based renderer.
These properties can be used with a Transform, or with the :func:`Render.add_property`
function.

.. _gl-blend-func:

``gl_blend_func``
    If present, this is expected to be a six-component tuple, which is
    used to set the equation used to blend the pixel being drawn with the
    pixel it is being drawn to, and the parameters to that equation.

    Specifically, this should be (`rgb_equation`, `src_rgb`, `dst_rgb`,
    `alpha_equation`, `src_alpha`, `dst_alpha`). These will be used to
    call::

        glBlendEquationSeparate(rgb_equation, alpha_equation)
        glBlendFuncSeparate(src_rgb, dst_rgb, src_alpha, dst_alpha)

    Please see the OpenGL documentation for what these functions do.
    OpenGL constants can be imported from renpy.uguu::

        init python:
            from renpy.uguu import GL_ONE, GL_ONE_MINUS_SRC_ALPHA

    The :tpref:`blend` transform property is generally an easy way to
    use this.

``gl_color_mask``
    This is expecting to be a 4-tuple of booleans, corresponding to the four
    channels of a pixel (red, green, blue, and alpha). If a given channel is
    True, the draw operation will write to that pixel. Otherwise, it will
    not.

``gl_cull_face``
    If not None, this enables face culling, which is the process of removing
    triangles that are facing away from the camera. To do this, we need to
    know which face is the front face, and which is the back face. The front face
    is determined using the winding order of the vertices, which is one of "cw" (clockwise) or "ccw" (counter-clockwise).
    The values are done in the Ren'Py coordinate system (with +Y down), which is the opposite of the OpenGL coordinate system.

``gl_depth``
    If True, this will clear the depth buffer, and then enable depth
    rendering for this displayable and the children of this displayable.

    Note that drawing any pixel, even transparent pixels, will update
    the depth buffer. As a result, using this with images that have
    transparency may lead to unexpected problems. (As an alternative,
    consider the ``zorder`` and ``behind`` clauses of the ``show`` statement.)

``gl_pixel_perfect``
    When True, Ren'Py will move the mesh such that the first vertex is aligned with
    a pixel on the screen. This is mostly used in conjunction with text,
    to ensure that the text remains sharp.

``gl_anisotropic``
    If supplied, this determines if the textures applied to a mesh are
    created with anisotropy. Anisotropy is a feature that causes multiple
    texels (texture pixels) to be sampled when a texture is zoomed by a
    different amount in X and Y.

    This defaults to True. Ren'Py sets this to False for certain effects,
    like the Pixellate transition.

``gl_texture_scaling``
    When supplied, this determines how the textures applied to a mesh
    are scaled. This expects one of the following string values:

    - "nearest"
    - "linear"
    - "nearest_mipmap_nearest"
    - "linear_mipmap_nearest"
    - "nearest_mipmap_linear"
    - "linear_mipmap_linear"

    This can also be customized for specific textures. `gl_texture_scaling_tex0` controls
    the first texture, `gl_texture_scaling_tex1` the second, `gl_texture_scaling_tex2`, the third,
    and `gl_texture_scaling_tex3` the fourth. While only these four are avalable through Transforms,
    it's possible to supply "texture_scaling_tex4" or "texture_scaling_myuniform" to Render.add_property.

``gl_texture_wrap``
    When supplied, this determines how the textures applied to a mesh
    are wrapped. This expects a 2-component tuple, where the first
    component is used to set GL_TEXTURE_WRAP_S and the second component
    is used to set GL_TEXTURE_WRAP_T, which conventionally are the X and Y
    axes of the created texture.

    The values should be OpenGL constants imported from renpy.uguu::

        init python:
            from renpy.uguu import GL_CLAMP_TO_EDGE, GL_MIRRORED_REPEAT, GL_REPEAT

    This can also be customized for specific textures. `gl_texture_wrap_tex0` controls
    the first texture, `gl_texture_wrap_tex1` the second, `gl_texture_wrap_tex2`, the third,
    and `gl_texture_wrap_tex3` the fourth. While only these four are avalable through Transforms,
    it's possible to supply "texture_wrap_tex4" or "texture_wrap_myuniform" to Render.add_property.


The following properties only take effect when a texture is being created,
by a Transform with :tpref:`mesh` set, or by :func:`Model`, where these
can be supplied the property method.

``gl_drawable_resolution``
    If True or not set, the texture is rendered at the same resolution
    as the window displaying the game. If False, it's rendered at the
    virtual resolution of the displayable.

``gl_depth_texture``
    If True, the texture also keeps the depth buffer of its render, which a
    shader samples by adding ``__depth`` to the sampler's name, as in
    ``tex0__depth``. The red channel holds the depth OpenGL stores, from 0.0
    at the near plane to 1.0 at the far plane, and 1.0 where nothing was
    drawn with :tpref:`gl_depth`. Under a ``perspective`` of (`n`, `p`, `f`),
    the distance from the camera is
    ``2.0 * n * f / (f + n - (2.0 * depth - 1.0) * (f - n))``.

    The framebuffer must accept a depth texture of a different size from its
    color buffer, as OpenGL 3, OpenGL ES 3, and WebGL 2 do. When it doesn't,
    Ren'Py logs that, and a shader that samples the depth raises an exception.

``gl_mesh_oversample_scale``
    A number that multiplies how much the texture is enlarged when the
    transforms above it zoom it up. The result is limited to the range from
    1.0 to :var:`config.mesh_oversample`. This defaults to 1.0. Setting it
    to 0.0 renders the texture at the size of the displayable, which keeps
    a low-resolution render blocky when it is enlarged with ``nearest``.


``gl_mipmap``
    If supplied, this determines if the textures supplied to a mesh are
    created with mipmaps. This defaults to True.

GLTFModel Displayable
-----------------------

The GLTFModel displayble allow you to load 3D models in the GLTF file format. This is what you should use
if you have a 3D model you created in another program and want to display in Ren'Py.

.. include:: inc/assimp

Some things we noticed:

* Some models are very small, and need to be zoomed by 100x or 1000x to be useful.
* The uniforms generating by each model can vary.

We recommend creating GLTF models with report=True, and then looking at log.txt to see
the size and  what's available. It's then possible to scale the model and to
create a suitable shader.

Standard materials
^^^^^^^^^^^^^^^^^^

``material=True`` selects the built-in ``renpy.gltf.unlit`` shader and the
standard glTF material import path::

    image room = GLTFModel("room.glb", material=True)

This displays base color, vertex color, opaque materials, cutouts, and alpha
blending without writing a shader. It is an unlit material preview; use the
PBR scene described below for lighting and metallic reflections.
For multiple models, enable ``gl_depth True`` on their shared camera.

The standard path supports ``TEXCOORD_0``, ``TEXCOORD_1``, ``COLOR_0``, texture
offset/rotation/scale from ``KHR_texture_transform``, and each texture's UV-set
selection, wrap modes, and minification/magnification filters. A texture that
references a missing UV set, or a set above 1, raises an error during loading.
Absent vertex colors default to white; models without texture coordinates
work when their materials have no textures.

Custom shaders can opt into the same contract with
``GLTFModel(..., material=True, shader="my.material")``. Available core textures
are:

======================== ============================ ===========
Sampler                  glTF source                  Sampling
======================== ============================ ===========
``u_tex_base_color``     Base color                   sRGB
``u_tex_metalness``      Packed metallic/roughness    Linear
``u_tex_normals``        Normal map                   Linear
``u_tex_lightmap``       Occlusion                    Linear
``u_tex_emissive``       Emissive                     sRGB
``u_tex_transmission``   Transmission (red)           Linear
``u_tex_thickness``      Volume thickness (green)     Linear
======================== ============================ ===========

The packed metallic/roughness texture stores roughness in green and metallic
in blue. All absent maps have white fallback textures, except the normal map,
which has a flat-normal fallback. Presence uniforms allow custom shaders to
distinguish authored textures from fallbacks.

Material uniforms always include the following defaults:

=================================== ===================
Uniform                             Default
=================================== ===================
``u_color_base``                    ``(1, 1, 1, 1)``
``u_material_metallicfactor``        ``1.0``
``u_material_roughnessfactor``       ``1.0``
``u_material_emissivefactor``        ``(0, 0, 0)``
``u_material_emissive_strength``     ``1.0``
``u_material_normal_scale``          ``1.0``
``u_material_occlusion_strength``    ``1.0``
``u_material_unlit``                 ``0.0``
``u_material_twosided``              ``0.0``
``u_material_gltf_alphamode``        ``0.0`` (opaque)
``u_material_gltf_alphacutoff``      ``0.5``
``u_material_receive_shadows``      ``1.0``
``u_material_transmission_factor``  ``0.0``
``u_material_thickness_factor``     ``0.0``
``u_material_ior``                  ``1.5``
``u_material_attenuation_distance`` ``0.0`` (infinite)
``u_material_attenuation_color``    ``(1, 1, 1)``
=================================== ===================

Each texture also supplies binding uniforms. For example, base color provides
``u_material_base_color_present`` (0 or 1),
``u_material_base_color_texcoord`` (0 or 1), and the ``vec3`` rows
``u_material_base_color_uv0`` and ``u_material_base_color_uv1``. Select
``a_tex_coord`` or ``a_tex_coord1``, form ``vec3(uv, 1.0)``, and take its dot
product with each row to obtain the transformed UV. The other samplers use
the same names with ``base_color`` replaced by ``metalness``, ``normals``,
``lightmap``, ``emissive``, ``transmission``, or ``thickness``. ``a_color``
contains linear RGBA vertex color.

Explicit model uniforms and texture overrides take precedence over imported
values. For example::

    image tinted_room = GLTFModel("room.glb", material=True, u_color_base=(0.5, 0.5, 1.0, 1.0))

Texture overrides accept image filenames or image manipulators. They inherit
the imported binding's sampler and color-space interpretation. Texture
bindings with different sampler settings have separate cached textures.

Color textures are decoded and filtered in linear space; alpha and data maps
remain linear. The built-in shader applies the linear material and vertex
factors, then converts RGB back to sRGB for Ren'Py's existing compositor and
premultiplies the output. Transparent composition continues in that display
color space outside a ``GLTFScene``. Inside that scene, the unlit shader
outputs linear light before the scene's tone mapping. sRGB textures require
OpenGL 2.1, the ``GL_EXT_texture_sRGB`` extension, or OpenGL ES 3.0.

The standard path is opt-in. Existing explicitly supplied shaders retain their
texture layout and interpretation unless ``material=True`` is passed.

PBR lighting and reflections
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Select ``shader="renpy.gltf.pbr"`` to use metallic/roughness materials with
directional, point, spot, and environment lighting. This enables the standard material
import path and tangent generation automatically. Wrap the shared camera in
``GLTFScene`` to render and blend the models in linear, half-float HDR::

    init python:
        _room_model = GLTFModel("room.glb", shader="renpy.gltf.pbr", zoom=100)
        _room_camera = Transform(
            Fixed(Transform(_room_model, xpos=640, ypos=360), xysize=(1280, 720)),
            xysize=(1280, 720), perspective=True, gl_depth=True)

    image lit_room = GLTFScene(
        _room_camera,
        light_direction=(0.3, -0.8, 1.0),
        light_color=(3.0, 2.8, 2.5),
        environment="lighting/studio.hdr",
        environment_intensity=1.0,
        meters_per_unit=0.01,
        exposure=1.0)

The shader uses GGX microfacets, Smith visibility, Schlick Fresnel, and a
Lambert diffuse term. It reads the core base-color, metallic/roughness,
normal, emissive, and occlusion textures with their individual UV sets and
transforms. Authored or generated tangents are used for an untransformed
normal map on UV0; other mappings derive a basis from the selected UVs.
Degenerate UV mappings leave the geometric normal unchanged. Normals use
the inverse transpose of the model transform, and double-sided surfaces
reverse their normals on back faces. Mirrored transforms correct the triangle
winding for both culling and lighting. ``KHR_materials_unlit`` bypasses lighting.
Occlusion affects indirect light; it does not darken direct light or emission.
``KHR_materials_emissive_strength`` supplies the nonnegative
``u_material_emissive_strength`` multiplier, independently of the emissive
color factor and texture. Explicit model overrides still take precedence.
This extension cannot be combined with ``KHR_materials_unlit``. Emission
brightens the material itself; it does not cast light on other objects.

The directional light and environment remain in scene coordinates as the
camera moves. ``light_direction`` points toward the light, with X right,
Y down, and Z toward the viewer. Colors and intensity multipliers are linear;
light and emissive values above 1 can produce HDR highlights. The scene
applies exposure, Reinhard tone mapping, sRGB encoding, and alpha
premultiplication after opaque and transparent materials have been composed.
The resulting image can be used with normal Ren'Py transforms and UI.

Environment images can be equirectangular sRGB images, such as PNG or JPEG,
or linear Radiance ``.hdr`` files. The top faces -Y, the center faces +X, and
three quarters across faces +Z. A cached GPU filter
builds six roughness bands, diffuse irradiance, and a split-sum BRDF lookup.
By default this uses 64 samples per texel at 256 by 128 pixels per band; the
resulting half-float atlas occupies about 2 MiB. ``environment_quality="medium"``
uses 128 samples at that resolution; ``"high"`` uses 256 samples at 512 by 256
pixels per band (8 MiB). Rough surfaces have broader reflections.
``environment_rotation`` rotates the probe around Y in degrees. The probe
supplies lighting, without drawing a sky or scene background.

Radiance files retain RGB values above 1 through source filtering,
reflections, and scene exposure. The loader supports flat RGBE pixels,
legacy and channel-based RLE, and both scanline axis orders and orientations.
Stored RGB samples are interpreted as linear working RGB; header exposure,
gamma, and color-correction metadata are not applied. Use the scene's
``environment_intensity`` and ``exposure`` settings to adjust the result.

HDR pixels use eight bytes each in the image cache and GPU texture, plus
the GPU mip chain. The file must fit the renderer's maximum texture
dimensions. The decoder also limits images to about 67 million pixels, with no
dimension above 32767. Values above the half-float maximum of 65504 raise
an error; reduce the source image's exposure before loading it. OpenEXR and
XYZE Radiance files are not supported. HDR environments can also be passed
as ``RawTexture("probe.hdr", premultiply=False, srgb=False)``; they do not
support manipulators that require a pygame surface.

High-frequency environment lighting remains limited by the filter's resolution
and sample count. Filtering and probe atlases share a renderer-owned 64 MiB LRU
cache. Camera movement reuses these immutable GPU textures; source image memory,
mipmaps, and textures retained by active frames are additional. GPU context
loss and image-cache clearing discard the filter cache.

For local reflections, pass up to two static box probes::

    define _room_probe = GLTFReflectionProbe(
        "lighting/room.hdr", position=(200, 150, 0),
        box_min=(0, 0, -200), box_max=(400, 300, 200), fade=25)

    image local_room = GLTFScene(
        _room_camera, environment="lighting/outdoors.hdr",
        reflection_probes=(_room_probe,))

All positions, bounds, and fade distances use scene units. Capture positions
must lie strictly inside their boxes. Reflection rays intersect the box before
sampling the captured map; diffuse lighting uses its direction without box
correction. A probe fades at box edges and contributes nothing outside its
box. Overlap weights normalize rather than increasing total energy. Each
probe accepts independent ``rotation`` and ``intensity``; the scene's
``environment_intensity`` also scales the combined result. Static maps cannot
reflect moving objects or provide reflection occlusion. There is no dynamic
scene capture. Probes share the existing environment sampler through an atlas.

Keep ordinary sprites and UI outside ``GLTFScene``: arbitrary custom shaders
must output linear premultiplied color to join its HDR composition. The
built-in unlit shader adapts automatically. Exposure is a multiplier, with
1 leaving scene brightness unchanged before tone mapping. Recreate the scene
displayable when changing its constructor settings.

``GLTFScene`` supports desktop only. Mobile and Web are unsupported. It
requires OpenGL 3.0 and half-float color attachments, and raises an error
if the renderer cannot create the HDR target. Existing custom shaders retain
their original import path. Scenes loaded from older saves retain their
previous lighting without automatically enabling imported lights, shadows,
or optical transmission.

Layered materials
^^^^^^^^^^^^^^^^^

The PBR shader supports ``KHR_materials_specular``, ``KHR_materials_clearcoat``,
and ``KHR_materials_sheen``. The loader validates their factors and imports
each map's UV selection, transform, and sampler independently. These extensions
cannot be combined with ``KHR_materials_unlit``. Their extra shader branches
activate only when their factors or textures change the base PBR response.

==================================== ======================== ========
Sampler                              Source channels          Sampling
==================================== ======================== ========
``u_tex_specular``                   Specular weight, alpha   Linear
``u_tex_specular_color``             Specular tint, RGB       sRGB
``u_tex_clearcoat``                  Coat amount, red         Linear
``u_tex_clearcoat_roughness``        Coat roughness, green    Linear
``u_tex_clearcoat_normals``          Coat normal, RGB         Linear
``u_tex_sheen_color``                Cloth tint, RGB          sRGB
``u_tex_sheen_roughness``            Cloth roughness, alpha   Linear
==================================== ======================== ========

These bindings use the same ``u_material_<binding>_present``, ``_texcoord``,
``_uv0``, and ``_uv1`` convention as core maps. Factor uniforms are
``u_material_specular_factor`` (default 1), ``u_material_specular_color_factor``
(white), ``u_material_clearcoat_factor`` (0),
``u_material_clearcoat_roughness_factor`` (0),
``u_material_clearcoat_normal_scale`` (1), ``u_material_sheen_color_factor``
(black), and ``u_material_sheen_roughness_factor`` (0). Specular color factors
may exceed 1; the final dielectric reflectance clamps before weight is applied.
All other scalar factors and sheen colors range from 0 to 1. Clearcoat normal
scale is finite but is not restricted to that range.

Specular adjusts dielectric weight/tint without suppressing metallic
reflections. Sheen adds a Charlie cloth lobe with energy compensation;
its environment approximation uses diffuse-filtered radiance and a numerical
directional-albedo lookup. Clearcoat has its own normal map and roughness,
and lies above both cloth shading and emission. Combining every map and lobe
can require 18 fragment samplers, exceeding some desktop GPUs' limits; the
driver rejects unsupported combinations. Ordinary PBR keeps its original
active sampler requirements. Anisotropy, iridescence, and specular-glossiness
materials remain unsupported.

Punctual lights and shadows
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The standard material importer reads ``KHR_lights_punctual`` from the active
glTF scene, including light-only assets. Directional, point, and spot lights
follow their source node hierarchy, model zoom, and enclosing transforms.
Camera transforms do not move world lights. Point and spot lights use
inverse-square attenuation, an optional range cutoff, and smooth spot cones.
Color is linear RGB; intensity uses lux for directional lights and candela
for point and spot lights. Node scale changes placement, not intensity or
the light's range in meters.

``GLTFScene`` uses imported lights by default. Its manual directional light
is additive; set ``light_color=(0, 0, 0)`` to use only authored lights.
``import_lights=False`` disables imported lights. Additional lights can be
placed beside models inside the camera or supplied in the scene's ``lights``
tuple::

    image lit_room = GLTFScene(
        _room_camera,
        light_color=(0, 0, 0),
        lights=(GLTFLight("point", position=(640, 250, 300),
            color=(1.0, 0.8, 0.6), intensity=100, range=8),),
        meters_per_unit=0.01)

Unlike the scene's manual ``light_direction``, a ``GLTFLight`` direction
points along the emitted light, defaulting to ``(0, 0, -1)``. Spot cone
half-angles use radians. The scene supports at most eight active lights,
including its manual directional light. Muted or black lights do not count.
Exceeding the limit raises an error. Recreate light and scene displayables
to change their settings, allowing Ren'Py's saves and rollback to restore
the previous displayables.

Use ``GLTFModel(..., light_overrides={"Lamp": {"intensity": 0}})`` to mute
a named imported light. Overrides can also change color, range, cone
angles, and shadow casting; they leave the shared model import unchanged.

Shadows are enabled by default. Each shadow-casting directional or spot
light uses one depth-atlas tile; a point light uses six. Spots with a
180-degree cone also use six tiles to avoid a degenerate projection.
``shadow_size``
requests the tile resolution, defaulting to 512 pixels, and shrinks when
needed to fit GPU limits. Increasing resolution and the number of shadowed
lights increases memory and draw cost. Meshes outside each light's frustum
are culled before shadow submission.
Shadows use a 3 by 3 PCF filter and receiver-plane depth correction.
``shadow_bias`` adjusts the remaining self-shadowing/detachment tradeoff.

``shadow_cascades=2`` or ``4`` assigns that many tiles to each directional
light, fitting static camera-depth intervals rather than the whole scene.
``shadow_distance`` sets their maximum depth in meters (default 50).
World-aligned texel snapping reduces shimmering, overlapping intervals
blend over their final ten percent, and the last interval fades to lit.
Cascades require one shared camera and multiply directional shadow draw
and storage cost. Point and spot maps are unchanged. The default of one
map retains the scene-bounds fit without a camera-distance cutoff.

Disable shadows with ``GLTFScene(..., shadows=False)`` or
``GLTFLight(..., cast_shadows=False)``. Models also accept ``cast_shadows``
and ``receive_shadows``. Opaque and masked texels cast shadows, including
opaque areas of a transmission map. Blended and transmitting texels do not
cast depth-only shadows. ``colored_shadows=True`` adds an HDR/depth atlas
with the two nearest glass or partial-coverage surfaces along each light
ray. Base tint, specular Fresnel and volume absorption multiply the light
reaching a receiver; opaque occlusion still takes precedence. This requires
shadows and transmission to be enabled, adds two fragment samplers, and
uses about 24 bytes per light-atlas pixel in the retained colored atlas,
plus temporary build targets. Tiles shrink to fit the doubled atlas height.
Deeper glass surfaces are omitted. Rays remain straight, geometric normals
set Fresnel, and volume thickness is applied per retained surface; closed
glass meshes may need thin-surface materials to avoid counting absorption
twice. Environment-light shadows are not implemented.

``caustics=True`` also enables colored shadows and redistributes transmitted
light from the nearest glass surface onto the first opaque receiver visible
in each light tile. A 64 by 64 cell grid traces 32 steps with six refinements,
then rasterizes a signed HDR correction: subtract straight transmitted flux
and add refracted flux, preserving an index-one pane's brightness. Roughness
suppresses the focused contribution, and direct-light brightening is capped
at four times the unoccluded value. This adds one fragment sampler, an
8-byte-per-pixel cached correction atlas, a temporary refracted-ray/depth
target, and up to two 8,192-triangle draws per light tile. Unchanged results
reuse the shadow cache; moving/deforming glass or receivers rebuilds them.

This bounded caustic approximation uses geometric normals and one refractive
interface. It cannot follow additional glass bounces, objects hidden behind
the nearest light-space depth, or rays leaving their light tile/point-light
face. Small casters can fall between grid samples, discontinuous receivers
can lose contributions, and half-float ray data limits volume travel to
65,504 meters. It does not replace baked or path-traced caustics. Reduce the
shadow distance or scene extent when the grid undersamples nearby glass.
Unchanged maps are reused by default. ``GLTFScene(..., shadow_cache=False)``
rebuilds them for comparison; :var:`config.gltf_shadow_cache_size_mb` bounds
retention. Geometry revisions, caster transforms and visibility, light
projections, cutout alpha/textures/UVs/samplers, and transmission opacity
invalidate affected faces. Camera movement and opaque RGB changes alone do
not invalidate single-map shadows. Cascades follow camera translation and
rotation. Directional-light bounds and local-light depth ranges
can change when any scene bounds move, invalidating additional faces.
Dynamic render textures and meshes without tracked buffer revisions bypass
reuse. Custom shadow deformation is not supported. Cached textures belong
to the renderer, are discarded on texture resets, and are never saved.
Colored maps also track glass RGB, specular, thickness and absorption data.

Custom shaders need ``material=True`` and its standard mesh/texture contract
to cast shadows; legacy custom meshes can still join the HDR scene.

Transmission and refraction
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The PBR shader supports ``KHR_materials_transmission``,
``KHR_materials_volume``, and ``KHR_materials_ior`` inside ``GLTFScene``.
Transmission describes light passing through a surface; alpha describes
coverage. Keep glass ``alphaMode="OPAQUE"`` for full coverage and use its
transmission factor to control optical transparency. ``MASK`` can cut holes
in glass, and ``BLEND`` still controls partial coverage. These extensions
cannot be combined with ``KHR_materials_unlit``.

Transmission uses the factor multiplied by the texture's red channel.
Metallic portions do not transmit light. Roughness blurs the transmitted
background; Fresnel reflectance and the index of refraction determine the
reflected and transmitted energy. ``ior`` defaults to 1.5, accepts values
of at least 1, and also supports the extension's special value 0 for full
Fresnel reflectance. IOR 1 gives no dielectric reflection or refraction.

Zero thickness gives thin-surface transmission without macroscopic bending.
Positive volume thickness, multiplied by the thickness texture's green
channel, enables Snell refraction and Beer-Lambert absorption. Thickness
is in local mesh units and follows model scale. Attenuation distance is in
meters, using the scene's ``meters_per_unit``; omitted distance means no
absorption. Volume materials should use closed manifold meshes with outward
normals. Their back faces remain visible when the camera is inside, including
total internal reflection above the critical angle. Thickness is an authored
approximation of the light path rather than a measured exit intersection.

Farther glass is included in the background of nearer glass. Each refractive
mesh samples a copy of the current linear HDR color and depth buffer.
Disjoint smooth, zero-thickness panes share a copy by default; overlaps,
roughness, volume displacement, and custom shaders retain separate copies.
``refraction_batch=False`` disables copy reuse for comparison. Mip chains
are refreshed only when rough transmission needs them. This still adds
framebuffer memory and GPU work. Refraction rejects foreground depth and falls back to the
environment when the projected ray leaves the scene or lacks coverage.
Use ``transmission=False`` on the scene to disable this optical pass.

``refraction_margin=0.25`` renders an extra quarter-view on every edge for
nearby offscreen samples. The default zero uses the visible frame only;
values up to 0.5 are supported. Guard regions preserve camera field of view,
original aspect bars and explicit crops. They align to drawable pixels,
including fractional DPI scaling, and shrink to the GPU's texture limits.
The scene still returns its original visible size. This requires one shared
camera projection and works with depth peeling and DoF.

The guard expands color/depth targets and draw work: a margin of 0.25 uses
about 2.25 times the pixels before other passes. A small viewport mask preserves
the original camera/crop boundary. It does not reveal geometry hidden behind
other surfaces or extend refraction beyond the expanded view. Rays leaving
that region still use the environment. Set the margin to zero to avoid its
extra work. Ordinary sprites and UI remain outside the scene.

This is screen-space refraction: hidden geometry, nested volume boundaries,
multiple internal bounces, dispersion, and scattering are not traced.
By default, meshes are sorted by their bounds, so intersecting glass can show
ordering artifacts. Depth peeling, described below, provides bounded per-pixel
ordering. Closed volumes with alpha blending can also show front/back
coverage artifacts; opaque coverage is recommended for solid glass.

Intersecting transparency
^^^^^^^^^^^^^^^^^^^^^^^^^

``GLTFScene(..., transparency_layers=4)`` enables depth peeling. The default
zero keeps mesh-center sorting; values from 1 to 8 retain that many nearest
transparent surfaces per pixel. Depth passes discover front-to-back layers,
then HDR color passes shade them back to front against completed farther
layers. This works for intersecting alpha-blended panes and refractive glass,
including foreground-depth rejection and geometry depth for DoF. Opaque and
masked surfaces occlude layers behind them. Layers beyond the requested count
are omitted, and coplanar surfaces remain ambiguous within depth24 precision.

Peeling requires one shared camera view/projection and built-in glTF material
shaders. Standard alpha and matrix-color transforms can accompany them.
Arbitrary transparent fragment-depth shaders and multiple independent cameras
are unsupported. Hidden geometry and multiple internal ray bounces are still
not traced. The layer-color shader needs one additional fragment sampler.

This option trades substantial memory and fill rate for correct ordering.
It adds a depth pass, a color/depth background copy, and a shading pass for
each requested layer. Four layers at 1920 by 1080 drawable pixels use roughly
180 MiB of intermediate targets, or about 200 MiB with refraction mipmaps,
in addition to other scene resources. Costs scale with drawable pixel count
and the layer limit; high-DPI output increases them. Filtering, opaque targets,
and all generated textures are accounted in renderer diagnostics.

Baked-room shaders and depth of field
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

An existing baked shader can join the linear HDR scene by appending
``renpy.gltf.baked_srgb`` to its shader tuple::

    _baked_room = GLTFModel("room.glb",
        shader=("mygame.baked_room", "renpy.gltf.baked_srgb"))

This adapter expects straight sRGB in the custom shader's final RGB channels.
It decodes those channels to linear light and makes coverage opaque. An alpha
channel containing legacy camera-depth data is ignored. Use the adapter only
inside ``GLTFScene``. The baked geometry still writes actual GPU depth, so PBR
glass in the same camera can refract it. The adapter does not convert custom
texture slots into standard materials or add dynamic lighting to a bake.

The scene's Reinhard tone mapping darkens and desaturates a bake that was
already display-referred. ``GLTFScene(..., tone_mapping=None)`` clips linear
color at 1.0 and encodes it as sRGB, so baked models drawn without lights or
exposure changes keep their original values::

    image baked_room = GLTFScene(_room_camera, light_color=(0, 0, 0),
        import_lights=False, shadows=False, tone_mapping=None)

For depth of field, enable ``GLTFScene(..., depth_output=True)`` and wrap it in
``GLTFDepthOfField``. Focus distance and range use meters, through the scene's
``meters_per_unit``. For example::

    image focused_room = GLTFDepthOfField(
        GLTFScene(_room_camera, meters_per_unit=0.01, depth_output=True),
        focus_distance=5.0, focus_range=1.0, max_blur=8.0)

Filtering gathers 16 samples in linear HDR before tone mapping. The maximum
radius is measured in drawable pixels and capped at 32. Subpixel radii below
a quarter pixel remain sharp, avoiding blur caused by depth reconstruction
noise. A closer depth discontinuity rejects background-gather samples. This
is an approximate defocus effect, not aperture ray tracing or full foreground
bokeh. All meshes must share one camera projection. Keep UI outside the effect,
and recreate the displayable to change its settings with save/rollback support.

Scene controls and animation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``GLTFModel(..., scene=True)`` retains the active glTF scene's named node
hierarchy, material identities, and node-transform animation. It enables the
standard material path. Existing imports and older saves remain static
unless this option or a scene-control option is selected.

Inspect ``model.scene_info()`` to get independent lists of nodes, materials,
and animation clips. Nodes report their source index, name, parent, children,
mesh index, camera index, and whether they belong to the active scene.
Clips report their index, name, duration in seconds, and supported channel
count. The returned dictionary also contains asset warnings. Changing the
returned data does not change a model or its shared import.

``report=True`` logs these names and indices with the usual model report.
Names must be unique within their category when used for lookup. Use numeric
source indices for unnamed objects or duplicate exporter names; ambiguous
names and unknown indices raise errors.

Node and material overrides are per model instance::

    image room_open = GLTFModel(
        "room.glb", scene=True, zoom=100,
        node_overrides={
            "Door": {"rotation": (0, 0.70710678, 0, 0.70710678)},
            "Clutter": {"visible": False},
        },
        material_overrides={
            "DoorPaint": {"u_color_base": (0.1, 0.3, 0.8, 1.0)},
            "Curtain": {"u_tex_base_color": "textures/curtain_blue.png"},
        })

Place the model inside a camera as in the examples above. The omitted shader
uses the unlit preview; select ``renpy.gltf.pbr`` inside ``GLTFScene`` for
lighting and optical materials.

Node settings replace local ``translation``, ``rotation``, ``scale``,
``matrix``, or morph ``weights`` values. They use glTF coordinates and mesh units
before model zoom: Y points up and rotation is an XYZW unit quaternion. Matrices contain
16 column-major numbers. A node cannot combine a matrix with TRS settings;
nodes authored with a matrix require a matrix override for transform changes.
Visibility changes also hide descendant meshes and lights. Changing a
parent's transform moves its descendants, including imported lights, and
updates shadow-caster placement and frustum culling.

Material settings accept the same uniforms and sampler-aware texture
overrides as whole-model settings. Precedence is source material, then
whole-model overrides, then the selected material's overrides. A material
override affects every primitive using that material; export distinct
materials when objects need independent material control. The override
dictionaries are copied at construction. Recreate a model to change them,
so script assignments can be restored by saves and rollback.

Models with the same import options share geometry, textures, hierarchy,
and clip data while keeping independent controls and poses. The optional
inactive-model cache budget includes an estimate of retained scene metadata.
Hiding an object does not unload its textures. ``node_overrides``,
``material_overrides``, or selecting an animation automatically enables
``scene=True``.

Node animation supports translation, quaternion rotation, scale, and morph weights::

    image room_opening = GLTFModel(
        "room.glb", scene=True, zoom=100,
        animation="DoorOpen", animation_loop=False)

    image room_halfway = GLTFModel(
        "room.glb", scene=True, zoom=100,
        animation="DoorOpen", animation_time=0.5)

Clips use STEP, LINEAR, or CUBICSPLINE interpolation. Rotation uses shortest
path spherical interpolation for LINEAR and normalized quaternion Hermite
interpolation for CUBICSPLINE. Animation buffers can be external local
resources, data URIs, or GLB BIN data; sparse accessors and normalized integer
rotation keys are supported. Each accessor is limited to one million samples.
Malformed timestamps, buffer ranges, quaternions, and channels raise errors
before native scene import.

Automatic playback uses elapsed shown-time, multiplied by
``animation_speed`` and shifted by ``animation_offset`` in seconds.
For reverse playback, use a negative speed and an offset at the clip's end.
``animation_time`` instead selects a fixed pose and bypasses speed and offset.
Clips hold their first and last keys outside their time range unless
``animation_loop=True`` is supplied. Node overrides are applied after the
selected clip, replacing the corresponding animated properties.

Running saved instances retain elapsed playback time, including named images
inside ``GLTFScene``. Ren'Py may re-execute a show statement while restoring a
checkpoint; such a statement can create a fresh instance. Reconstructed
screens can also create new instances. Fixed ``animation_time`` poses give
explicit control when exact seeking or paused state is required.

Unsupported optional extensions produce diagnostic warnings; unsupported required
extensions raise an error in the scene path. ``KHR_animation_pointer``, additive
animation layers, animation retargeting, and inverse kinematics are not implemented.

Skinning, morph targets, and clip blending
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Scene-enabled models deform positions, normals, and tangents on the CPU before
normal rendering, culling, shadows, and optical materials. Morph deltas are
applied before linear-blend skinning. Skin indices refer to source joint nodes,
including rigs with duplicate joint names. All ``JOINTS_n``/``WEIGHTS_n`` sets
contribute; influences are normalized without reducing them to four joints.
Missing inverse-bind matrices mean identity. Skinned primitives use joint-world
transforms and the skeleton root's winding, ignoring the instantiating mesh
node's transform, as specified by glTF.

Node morph weights override mesh defaults, which otherwise default to zero.
They may be negative or greater than one. Their count must match the target
count on every primitive. ``node_overrides={"Face": {"weights": (0.5, 0.0)}}``
overrides animated weights for that node. Weight clips support STEP, LINEAR,
and CUBICSPLINE, including sparse accessors. Position, normal, and tangent
targets retain the source vertex identities. Missing base normals are generated;
missing authored tangents use the shader's derivative basis. Tangent morphs
require authored base tangents. Triangle lists, strips, and fans are supported.
Accessors are limited to one million elements; morph delta storage is limited
to 256 MB per primitive. Invalid skin indices, stream lengths, or zero-total
joint weights raise errors during loading.

Blend two clips or crossfade between them::

    image walking_to_idle = GLTFModel(
        "character.glb", scene=True, shader="renpy.gltf.pbr",
        animation="Walk", animation_loop=True,
        animation_blend=("Idle", 1.0), animation_blend_duration=0.3)

``animation_blend=(clip, weight)`` blends from the primary clip to the secondary
clip, with a weight from zero to one. Missing animated channels fall back to
their bind-pose values; rotations use shortest-path spherical interpolation.
The secondary clip shares primary time unless ``animation_blend_time`` pins it
independently. ``animation_blend_duration`` ramps from zero to the requested
weight over elapsed shown seconds. It is independent of clip speed, offset,
and pinned clip time; zero applies the weight immediately. Node overrides
apply after blending. Recreate displayables to change these saved controls.

Deformation data, latest per-instance poses, and native meshes remain outside
saved displayables. Unchanged poses reuse their meshes; changed poses publish
fresh meshes with updated bounds without modifying older rendered frames.
The model cache accounts for retained deformation streams and latest pose
buffers. Source files are unchanged: an in-memory static copy supplies Assimp's
material/texture import while the engine retains authored deformation identities.
CPU work and GPU uploads grow with vertex count, active morph targets, and
joint influences. There is no GPU skinning or dual-quaternion skinning.

Imported cameras
^^^^^^^^^^^^^^^^

``GLTFCamera`` renders through a camera authored in a scene-enabled model::

    _room = GLTFModel("room.glb", scene=True, shader="renpy.gltf.pbr", zoom=100)
    image authored_room = GLTFScene(
        GLTFCamera(_room, camera="MainCamera"), meters_per_unit=0.01)

The camera uses the model's source coordinates and zoom. Do not add a manual
screen-centering offset or a separate perspective transform. An optional second
argument supplies scene contents independently of the camera-source model.
Camera pose follows source node overrides and animation, including camera-only
sources that are not drawn. Node scale is removed from the camera axes and
does not change field of view.

Select a camera definition by its unique name or numeric index. ``node=``
selects a particular active camera node when a definition has multiple
instances. With neither selector, the first active instance is used.
``model.scene_info()["cameras"]`` reports validated projection settings and
the active nodes instancing each definition. Recreate the camera to change
selection; its source and selection survive saves and rollback.

Perspective cameras use authored vertical field of view and near/far planes;
an omitted far plane uses an infinite projection. An omitted aspect ratio uses
the offered rectangle. Authored aspect ratios and orthographic magnification
fit inside that rectangle with transparent bars. Negative nonzero orthographic
magnification mirrors the corresponding view axis. PBR view vectors and depth
of field support both projection types. Degenerate node axes, invalid clip
planes, and unsupported perspective fields of view at or above pi raise errors.
These settings follow the `glTF camera definitions
<https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#cameras>`_.

Desktop scene benchmark
^^^^^^^^^^^^^^^^^^^^^^^

The source testcase project includes a synthetic furnished room with 73
nodes, six shared mesh buffers, an animated door, local lights, and glass.
Run its optional benchmark from the repository root::

    ./run.sh testcases test assimp_scene_benchmark --report-detailed

On Windows, use ``.\run.ps1``. The benchmark warms five frames and samples
30 frames per mode, comparing cached and uncached shadows for animated
opaque, shadowed, and refractive scenes, plus static shadowed and refractive
scenes. It checks identical cached/uncached output and profiles ten
additional frames separately. Offscreen targets undergo render garbage
collection between frames. It writes ``gltf-benchmark.json`` and a preview
PNG to the project's save directory. The report includes renderer information,
requested and readback resolution, import/cache status, median and 95th
percentile render-plus-readback timings, shared geometry counts, estimated
metadata size, renderer-wide texture allocation snapshots, pass profiles,
and shadow-cache counters.

These timings include synchronized GPU readback; they are not interactive
frame times or FPS estimates. The fixture exercises the feature combination
with simple geometry. Repeat with representative game assets before drawing
conclusions about game performance, and compare results on the same machine
at the same resolution.

Custom alpha shaders
^^^^^^^^^^^^^^^^^^^^

Existing custom shaders can use ``material_alpha=True`` to enable alpha modes
with the legacy import uniforms. Here is a simple example::

    init python:

        renpy.register_shader("example.material", variables="""
            uniform vec4 u_color_diffuse;
            uniform sampler2D u_tex_diffuse;
            uniform float u_material_gltf_alphamode;
            uniform float u_material_gltf_alphacutoff;

            out vec2 v_tex_coord;

            in vec2 a_tex_coord;
    """, vertex_201="""
            v_tex_coord = a_tex_coord;
    """, fragment_201="""
            vec4 base = texture(u_tex_diffuse, v_tex_coord) * u_color_diffuse;

            if (u_material_gltf_alphamode < 0.5) {
                base.a = 1.0;
            } else if (u_material_gltf_alphamode < 1.5) {
                if (base.a < u_material_gltf_alphacutoff) {
                    discard;
                }
                base.a = 1.0;
            }

            fragment_color = vec4(base.rgb * base.a, base.a);
    """)

    image glass_scene = GLTFModel("scene.glb", shader="example.material", material_alpha=True)

This unlit shader preserves material colors and implements the alpha modes.
Lighting can be added before multiplying the final RGB by alpha.

With ``material_alpha=True``, material textures contain straight alpha. Shaders
must premultiply their final RGB by their final alpha, as above. The uniform
``u_material_gltf_alphamode`` is 0 for ``OPAQUE``, 1 for ``MASK``, and 2 for
``BLEND``. ``u_material_gltf_alphacutoff`` defaults to 0.5. Opaque materials
ignore texture and material alpha; masked materials discard pixels below the
cutoff and render the remaining pixels as opaque.

Blended meshes are drawn after opaque and masked geometry, with depth testing
enabled and depth writes disabled. They are sorted back to front by the centers
of their bounds in camera space. For multiple GLTFModels, set ``gl_depth True``
on their shared camera or parent so they share a transparent pass.

This sorts meshes rather than individual triangles. Intersecting or concave
transparent meshes can still show ordering artifacts; splitting glass into
separate panes can help. Inside ``GLTFScene``, ``transparency_layers`` enables
the per-pixel path described above. Shaders that displace vertices beyond the imported
bounds can also invalidate the sort order. Refraction and
``KHR_materials_transmission`` are not implemented by this alpha blending path.

The default ``material_alpha=False`` preserves premultiplied textures and the
original draw order for existing shaders and saves. Switch the shader and this
option together. Displayables supplied as texture overrides must also provide
straight-alpha texture data when the option is enabled.

Visibility and texture diagnostics
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``GLTFModel(..., frustum_cull=True)`` skips meshes whose imported bounding boxes
are wholly outside the camera's view. Bounds that intersect the view remain
visible. Leave this disabled for shaders that move vertices outside those
boxes. Culling reduces geometry submission; it does not unload textures or
change which textures prediction loads.

The source tree includes ``scripts/gltf_texture_report.py``, which requires
Pillow. Pass external-texture GLB files to report their texture sizes, shared
resources, full RGBA mip chains, and the combined working sets of scene pairs::

    python scripts/gltf_texture_report.py day.glb night.glb --output textures.json

The report identifies single-color RGB/RGBA maps without metadata as candidates
for a separate asset review. It does not modify source files. These are asset
estimates, not process or GPU memory measurements.
Byte-identical images are counted once. Aliases with different filenames need
to share a filename to share an image-cache entry.

To create an independent staging copy of an external-texture GLB, use::

    python -m scripts.gltf_texture_stage assets/room.glb staging/room
    python -m scripts.gltf_texture_stage assets/room.glb staging/small --max-size 2048

This optional Pillow tool never writes into the source directory or replaces
an existing output directory. It shrinks metadata-free constant RGB/RGBA PNGs
to one pixel and shares byte-identical resource files. The optional size cap
is lossy: color maps are filtered in linear light with premultiplied alpha,
while data-map channels are filtered independently. Resizing rejects maps with
ambiguous color/data use, metadata, or unsupported pixel formats. Compressed
resources are copied unchanged. The tool requires external images; embedded
images must first be externalized. Geometry chunks are preserved.

Material bindings and static node transforms are prepared once in unsaved
import data. Prediction requests the actual sampler-aware overrides rather
than an additional unused parent texture. These caches do not hold GPU
resources on saved displayables.

For engine profiling, ``config.debug_image_cache = True`` enables cache counters
and CPU timings for decoding, upload steps, and mipmap submission. Internal
diagnostic snapshots are available after the renderer starts::

    import renpy.display.im as _im
    import renpy.display as _display
    import renpy.gl2.assimp as _assimp
    import renpy.gl2.gl2draw as _gl2draw

    _im.cache.get_diagnostics()
    _assimp.get_diagnostics()
    _gl2draw.model_stats.copy()
    _display.draw.texture_loader.get_diagnostics()

Cache snapshots report files, cache accounting, current-generation protection,
and whether GPU upload has completed when that information is available.
Upload snapshots include renderer-wide texture allocations and pending work.
The counters accumulate over the lifetime of their cache or renderer. Compare
snapshots around an operation, and repeat timing runs with profiling disabled.
Submission timings do not measure GPU execution or display presentation.

For pass instrumentation, enable the internal renderer profiler after the
display starts, on the render thread::

    import renpy.display as _display
    _display.draw.set_gltf_profiling(True)
    # Render the frames being measured.
    _report = _display.draw.get_gltf_profile(reset=True)
    _display.draw.set_gltf_profiling(False)

The snapshot contains per-pass CPU milliseconds, draw/triangle/culling
counts, refraction copies, and GPU milliseconds when desktop timestamp
queries are available. Categories include scene preparation, opaque and
blended meshes, shadow faces, shadow-atlas assembly, transmission,
refraction copies, environment filtering, output, and other draws.
Scene preparation includes child rendering and shadow-cache lookup; draw
CPU timings measure state setup and submission. They are not a complete
frame breakdown. Cache counters and retained bytes are available from
``_display.draw.texture_loader.shadow_cache.snapshot()``.

GPU timestamps bracket adjacent draws in the same category. They measure
elapsed GPU timeline intervals, including gaps between commands, rather
than GPU utilization. Results are polled without waiting; ``gpu_pending``
reports intervals whose results are not ready. At most 256 intervals may
be pending; overflow increments ``gpu_dropped``. Reset discards pending
samples from the old measurement window. Unsupported GPU timers leave CPU
profiling available. Profiling is off by default and adds overhead when
enabled, so compare uninstrumented runs separately.



Model Displayable
-----------------

The Model displayable acts as a factory to created models for use with the
model-based renderer. This is an older API you may wish to use when you want to create
models in Python.

.. include:: inc/model_displayable

Model Displayable Examples
^^^^^^^^^^^^^^^^^^^^^^^^^^

The Model displayable can be used in conjunction with an ATL transform and
a built-in shader to create the Dissolve transform::

    transform dt(delay=1.0, new_widget=None, old_widget=None):
        delay delay
        Model().texture(old_widget).child(new_widget)
        shader [ 'renpy.dissolve' ]

        u_renpy_dissolve 0.0
        linear delay u_renpy_dissolve 1.0

Using the Model displayable as the child of a displayable is incompatible
with :tpref:`mesh`, as the two both create models inside Ren'Py.

Animated Shaders
----------------

When using shaders that depend on ``u_time`` to animate, one must be aware,
that even though every shader on screen will run on every frame displayed,
Ren'Py does not run on constant FPS, and will fall back to the minimum frame
rate of 5 FPS if no displayables require to be redrawn.

When using an animating shader in an ATL transform, this can cause that shader
to "stutter" and only animate properly while some other object on screen
animates as well, in case the transform you're using it in does not cause
redraws otherwise. In this case, an empty ATL loop can be introduced to force
redraws to happen::

    transform fancy_shader:
        shader 'my_fancy_shader'
        pause 0
        repeat

``pause 0`` will cycle the frames as fast as possible. You can also set
different values for ``pause`` to specify a minimum frame rate, like
``pause 1.0/30``.


Shader Parts
------------

For a list of shader parts that Ren'Py uses, see the :doc:`shader_parts`.
