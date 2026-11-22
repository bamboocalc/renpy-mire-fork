import ctypes
import pickle
from types import SimpleNamespace

import pytest

import renpy
from renpy.audio import audio
from renpy.audio import filter as audio_filter


@pytest.fixture(scope="module")
def apply_filter():
    """
    Return a ctypes wrapper around the Cython `get_apply_audio_filter` C entry point.

    The entry point is exported as a PyCapsule, so the function pointer is extracted with the
    PyCapsule C API. The wrapper takes (filter, float buffer, channels, frame count, sample rate).
    """

    capsule = audio_filter.__pyx_capi__["get_apply_audio_filter"]
    get_name = ctypes.pythonapi.PyCapsule_GetName
    get_name.argtypes = [ctypes.py_object]
    get_name.restype = ctypes.c_char_p
    get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
    get_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
    get_pointer.restype = ctypes.c_void_p
    getter = ctypes.PYFUNCTYPE(ctypes.c_void_p)(get_pointer(capsule, get_name(capsule)))
    return ctypes.PYFUNCTYPE(
        None, ctypes.py_object, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int, ctypes.c_int
    )(getter())


def apply(apply_filter, filter, samples):
    """
    Run `samples` (interleaved stereo floats) through `filter` at 1000 Hz and return the result.

    The filter is prepared before each call. The returned list is a copy of the ctypes buffer
    after the in-place update.
    """

    buffer = (ctypes.c_float * len(samples))(*samples)
    filter.prepare(1000)
    apply_filter(filter, buffer, 2, len(samples) // 2, 1000)
    return list(buffer)


@pytest.mark.parametrize(
    ("delay", "channel", "frame"),
    [
        (0.02, 0, 20),
        (0.02, 1, 20),
        ([0.02, 0.03], 0, 20),
        ([0.02, 0.03], 1, 30),
    ],
    ids=["single-left", "single-right", "multi-left", "multi-right"],
)
def test_delay_keeps_stereo_channels_separate(apply_filter, delay, channel, frame):
    """
    An impulse on one channel is delayed only on that channel, so the other channel stays silent.

    Scalar and per-channel delay lists are both covered. Samples are interleaved, so the expected
    index of frame N on channel C is N * 2 + C.
    """

    samples = [0.0] * 100
    samples[channel] = 1.0
    expected = [0.0] * 100
    expected[frame * 2 + channel] = 1.0
    assert apply(apply_filter, audio_filter.Delay(delay), samples) == expected


@pytest.mark.parametrize(("delay", "rate"), [([-0.5, 0.2], 1000), (0.2, 0)], ids=["negative", "no-rate"])
def test_delay_rejects_settings_that_index_outside_its_ring(delay, rate):
    """A negative delay or a zero sample rate would make the native ring buffer read and write out of bounds."""

    with pytest.raises(ValueError):
        audio_filter.Delay(delay).prepare(rate)


def test_delay_wraps_and_preserves_state_between_calls(apply_filter):
    """
    A Delay keeps its internal buffer across successive calls, so chunked input matches one large call.

    Impulses are placed at several frames spread across the input, so some echoes land in later
    chunks. The input is fed in 14-sample chunks, and the concatenated output must equal the expected
    delayed signal, which checks state continuity across calls.
    """

    samples = [0.0] * 6200
    expected = [0.0] * 6200
    for frame in (0, 1009, 2050):
        for channel, delay in ((0, 20), (1, 30)):
            samples[frame * 2 + channel] = channel + 1.0
            expected[(frame + delay) * 2 + channel] = channel + 1.0

    filter = audio_filter.Delay([0.02, 0.03])
    output = []
    for start in range(0, len(samples), 14):
        output.extend(apply(apply_filter, filter, samples[start : start + 14]))
    assert output == expected


@pytest.mark.parametrize("kwargs", [{}, {"wet": True}], ids=["default-wet", "explicit-wet"])
def test_comb_wet_preserves_input_with_zero_multiplier(apply_filter, kwargs):
    """
    A wet Comb with a zero multiplier passes the input through unchanged.

    The default and explicit `wet=True` forms are both wet, so both must return the input samples.
    """

    samples = [1.0, -0.5, 0.25, 0.75] * 20
    assert apply(apply_filter, audio_filter.Comb(0.02, multiplier=0, **kwargs), samples) == samples


def test_comb_without_wet_returns_only_delayed_signal(apply_filter):
    """
    A dry-only Comb (`wet=False`) outputs only the feedback taps, so the original impulse is absent.

    At 1000 Hz a 20 ms delay is 20 frames, which is 40 interleaved samples. With multiplier 0.5 the
    impulse reappears at sample 40 with amplitude 0.5 and at sample 80 with amplitude 0.25.
    """

    samples = [0.0] * 120
    samples[0] = 1.0
    expected = [0.0] * 120
    expected[40] = 0.5
    expected[80] = 0.25
    assert apply(apply_filter, audio_filter.Comb(0.02, multiplier=0.5, wet=False), samples) == expected


@pytest.mark.parametrize("wet", [True, False], ids=["wet", "dry"])
def test_comb_serialization_preserves_wet(apply_filter, wet):
    """
    The `wet` setting survives a pickle round trip and still controls the output after restore.

    `__reduce__` stores `wet` as the last constructor argument, which the test reads directly. A wet
    restored filter passes input through, and a dry one outputs silence for this zero-multiplier case.
    """

    filter = audio_filter.Comb(0.02, multiplier=0, wet=wet)
    assert filter.__reduce__()[1][-1] == wet
    restored = pickle.loads(pickle.dumps(filter))
    assert restored.__reduce__()[1][-1] == wet
    samples = [1.0, -0.5] * 20
    assert apply(apply_filter, restored, samples) == (samples if wet else [0.0] * len(samples))


@pytest.mark.parametrize("channel", [0, 1], ids=["left", "right"])
def test_reverb_keeps_stereo_channels_separate(apply_filter, channel):
    """
    A reverb excited on one channel produces a tail only on that channel, never on the other.

    The dry signal is zeroed, so output comes only from the reverb network. The silent channel must be
    all zeros, and the active channel must contain energy. The first 40 interleaved samples (20 frames)
    are checked to be silent, because the 20 ms delay line has not yet produced output.
    """

    samples = [0.0] * 160
    samples[channel] = 1.0
    filter = audio_filter.Reverb(dampening=100, dry=0, delay_times=[0.02], allpass_frequences=[])
    output = apply(apply_filter, filter, samples)
    assert output[1 - channel :: 2] == [0.0] * 80
    assert output[:40] == [0.0] * 40
    assert any(output[channel::2])


class FilterChannel(audio.Channel):
    """A channel with its own music context instead of one taken from a running game."""

    context = None

    def interact(self):
        pass


@pytest.fixture
def new_channel(mocker):
    """Make channels whose filters are prepared at 1000 Hz, as renpysound does when it's handed one."""

    def prepare(number, filter, playing):
        if filter is not None:
            filter.prepare(1000)

    mocker.patch.object(renpy.game, "log", SimpleNamespace(mutated={}))
    mocker.patch.object(audio, "pcm_ok", True)
    mocker.patch.object(audio.renpysound, "replace_audio_filter", side_effect=prepare)

    def make():
        channel = FilterChannel.__new__(FilterChannel)
        channel.context = audio.MusicContext.__new__(audio.MusicContext)
        channel.context.last_changed = 0
        channel.context.last_filenames = []
        channel.context.last_tight = False
        channel.context.last_relative_volume = 1.0
        channel.last_changed = 0
        channel._number = 0
        channel.name = "test"
        channel.queue = []
        channel.loop = []
        return channel

    return make


def play(apply_filter, channel, samples):
    """Run samples through the filter that a channel last handed to renpysound."""

    if channel.context.audio_filter is None:
        return list(samples)

    return apply(apply_filter, channel.context.audio_filter, samples)


def tone(frames, start=0):
    """A stereo signal with no period that fits the 10 ms chunks."""

    return [((i * 7919) % 1009) / 2018 for i in range(start, start + frames) for _channel in range(2)]


def definition(kind):
    """Make a new filter definition like one a game keeps in a variable."""

    if kind == "highpass":
        return [audio_filter.Highpass(100)]

    # The default allpass frequencies are unstable above the 500 Hz limit of this test rate.
    return [audio_filter.Reverb(0.7, dampening=150, dry=0.25, allpass_frequences=[225, 341])]


@pytest.mark.parametrize("kind", ["highpass", "reverb"])
@pytest.mark.parametrize("equal_copy", [False, True], ids=["same-objects", "equal-copy"])
@pytest.mark.parametrize("replace", [False, True], ids=["queued", "replace"])
def test_setting_the_same_filter_again_keeps_it_playing(apply_filter, new_channel, kind, equal_copy, replace):
    """Re-entering a scene sets the filter it already has, which must not step, restart, or fade that filter."""

    first = definition(kind)
    channel = new_channel()
    channel.set_audio_filter(audio_filter.to_audio_filter(first), replace=True, duration=0.1)
    steady = new_channel()
    steady.set_audio_filter(audio_filter.to_audio_filter(definition(kind)), replace=True, duration=0.1)
    assert play(apply_filter, channel, tone(300)) == play(apply_filter, steady, tone(300))

    again = definition(kind) if equal_copy else first
    channel.set_audio_filter(audio_filter.to_audio_filter(again), replace=replace, duration=0.5)

    assert play(apply_filter, channel, tone(700, 300)) == play(apply_filter, steady, tone(700, 300))


@pytest.mark.parametrize("kind", ["highpass", "reverb"])
def test_channels_given_one_filter_do_not_share_its_state(apply_filter, new_channel, kind):
    """One definition on two channels must sound the same as it does on one."""

    shared = audio_filter.to_audio_filter(definition(kind))
    first, second, alone = new_channel(), new_channel(), new_channel()
    first.set_audio_filter(shared, replace=True, duration=0)
    second.set_audio_filter(shared, replace=True, duration=0)
    alone.set_audio_filter(audio_filter.to_audio_filter(definition(kind)), replace=True, duration=0)

    for start in range(0, 300, 10):
        assert play(apply_filter, first, tone(10, start)) == play(apply_filter, alone, tone(10, start))
        play(apply_filter, second, [0.0] * 20)


def test_fade_between_filters_that_share_a_child(apply_filter, new_channel):
    """A new filter that reuses a child of the playing one must not step that child twice."""

    shared = audio_filter.Highpass(100)
    channel, separate = new_channel(), new_channel()
    channel.set_audio_filter(shared, replace=True, duration=0)
    separate.set_audio_filter(audio_filter.Highpass(100), replace=True, duration=0)
    assert play(apply_filter, channel, tone(100)) == play(apply_filter, separate, tone(100))

    changed = [shared, audio_filter.Multiply(0.5)]
    channel.set_audio_filter(audio_filter.to_audio_filter(changed), replace=True, duration=0.2)
    changed = [audio_filter.Highpass(100), audio_filter.Multiply(0.5)]
    separate.set_audio_filter(audio_filter.to_audio_filter(changed), replace=True, duration=0.2)

    assert play(apply_filter, channel, tone(200, 100)) == play(apply_filter, separate, tone(200, 100))


@pytest.mark.parametrize("target", [1.0, None], ids=["filter", "none"])
def test_interrupted_fade_continues_from_its_mix(apply_filter, new_channel, target):
    """A change halfway through a fade starts at what was audible, so the output has no step."""

    channel = new_channel()
    channel.set_audio_filter(audio_filter.Multiply(0.0), replace=True, duration=0.2)
    before = play(apply_filter, channel, [1.0] * 200)
    assert before[-1] == pytest.approx(0.505, abs=0.01)

    new = None if target is None else audio_filter.Multiply(target)
    channel.set_audio_filter(new, replace=True, duration=0.2)

    after = play(apply_filter, channel, [1.0] * 20)
    assert after[0] == pytest.approx(before[-1], abs=0.02)


@pytest.mark.parametrize("started", [False, True], ids=["unstarted", "halfway"])
def test_removing_a_filter_twice_lets_its_fade_finish(apply_filter, new_channel, started):
    """A second None must not cut a fade that is still removing the filter."""

    channel = new_channel()
    channel.set_audio_filter(audio_filter.Multiply(0.0), replace=True, duration=0)
    play(apply_filter, channel, [1.0] * 20)
    channel.set_audio_filter(None, replace=True, duration=0.1)
    expected = play(apply_filter, channel, [1.0] * 100)[-1] if started else 0.0

    channel.set_audio_filter(None, replace=True, duration=0.1)

    assert play(apply_filter, channel, [1.0] * 20)[0] == pytest.approx(expected, abs=0.02)
    play(apply_filter, channel, [1.0] * 400)
    channel.set_audio_filter(None, replace=True)
    assert channel.context.audio_filter is None


@pytest.mark.parametrize("last", [0.5, 0.25], ids=["same", "changed"])
def test_replacing_a_queued_change_fades_from_the_playing_filter(apply_filter, new_channel, last):
    """A change made with replace=False hasn't been heard yet, so a fade can't start from it."""

    channel = new_channel()
    channel.set_audio_filter(audio_filter.Multiply(0.0), replace=True, duration=0)
    play(apply_filter, channel, [1.0] * 20)
    channel.set_audio_filter(audio_filter.Multiply(0.5), replace=False, duration=0.1)

    channel.set_audio_filter(audio_filter.Multiply(last), replace=True, duration=0.1)

    assert play(apply_filter, channel, [1.0] * 20)[0] == pytest.approx(0.0)


def test_unheard_changes_do_not_pile_up(new_channel):
    """Changes made while nothing plays must not nest a fade for each call."""

    channel = new_channel()

    for i in range(100):
        channel.set_audio_filter(audio_filter.Multiply(i), replace=True, duration=1.0)

    assert not isinstance(channel.context.audio_filter.filter1, audio_filter.Crossfade)


def test_queue_gets_the_channels_filter(new_channel):
    """Queued tracks use the same filter the mixer was given."""

    channel = new_channel()
    entry = audio.QueueEntry("next.ogg", 0, False, True, 1.0, None)
    channel.queue = [entry]

    channel.set_audio_filter(audio_filter.Multiply(0.5))

    assert entry.audio_filter is channel.context.audio_filter
    assert audio.renpysound.replace_audio_filter.call_args.args == (0, entry.audio_filter, 0)


@pytest.mark.parametrize("restored", ["rollback", "load"])
def test_restored_context_fades_from_the_playing_filter(apply_filter, new_channel, mocker, restored):
    """Rollback and load hand interact the filter to return to, and the fade starts from the one being heard."""

    saved = audio_filter.Multiply(0.75)
    channel = new_channel()
    channel.set_audio_filter(audio_filter.Multiply(0.0), replace=True, duration=0)
    play(apply_filter, channel, [1.0] * 20)
    channel.context.last_changed = 1
    channel.context.raw_audio_filter = saved if restored == "rollback" else pickle.loads(pickle.dumps(saved))
    channel.context.audio_filter = channel.context.raw_audio_filter
    mocker.patch.object(audio, "all_channels", [channel])
    mocker.patch.object(audio, "periodic")
    mocker.patch.object(audio.renpy.config, "debug_sound", True)
    mocker.patch.object(audio.renpy.display.video, "last_channel_movie", {"test": True})

    audio.interact()

    assert play(apply_filter, channel, [1.0] * 100)[0] == pytest.approx(0.0)
    assert channel.context.raw_audio_filter is channel.raw_audio_filter


def test_filter_settings_from_script_lists_can_be_copied(apply_filter, new_channel):
    """Filter arguments written in script are revertable lists."""

    delays = renpy.revertable.RevertableList([0.02, 0.03])
    channel = new_channel()
    channel.set_audio_filter(audio_filter.Delay(delays), replace=True, duration=0)

    assert play(apply_filter, channel, [1.0] * 100)[40:42] == [1.0, 0.0]
