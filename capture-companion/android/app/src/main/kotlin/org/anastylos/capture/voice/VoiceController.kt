package org.anastylos.capture.voice

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.util.Locale
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Log tag for voice diagnostics: counts and timings only, never audio or transcripts. */
internal const val VOICE_LOG_TAG = "AnastylosVoice"

/**
 * Opt-in hands-free voice on the phone microphone. One reader thread owns
 * [AudioRecord] (16 kHz mono PCM, VOICE_RECOGNITION source); the camera
 * pipeline is untouched. The phone is deaf while it speaks and while
 * push-to-talk records. While a clip is in flight it keeps segmenting but
 * holds at most the next segment, processed only if the in-flight clip
 * turns out to be a bare wake word; everything else is dropped, never
 * queued (TL review of 6f9edcef, point 4). Segments live only in memory
 * until sent; nothing is written to storage.
 */
class VoiceController(
    context: Context,
    link: VoiceLink = DetachedVoiceLink(),
) {
    /** Swapped by the guided screen: the paired receiver link while a remote session is attached. */
    @Volatile var link: VoiceLink = link
        set(value) {
            field = value
            refreshWakeNames()
        }

    /** What counts as the wake word: the workstation's agent name, or "Muse" / "Meta" without one or on failure. */
    @Volatile private var wakeNames: WakeWord.Names = WakeWord.Names.DEFAULT
    data class State(
        val listening: Boolean = false,
        val pushToTalk: Boolean = false,
        val busy: Boolean = false,
        val speaking: Boolean = false,
        val status: String = "",
        val segments: Int = 0,
        val backgroundRms: Double = 0.0,
        val listeningSinceMs: Long? = null,
        /** Last addressed utterance, shown transiently; never persisted. */
        val heard: String? = null,
    )

    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    @Volatile private var reader: Thread? = null
    @Volatile private var armedUntilMs = 0L
    private var segmentCount = 0L
    /** The one segment captured while a clip was in flight; see the class comment. */
    private var held: Segment? = null
    private var pttSamples: MutableList<ShortArray>? = null
    private var tts: TextToSpeech? = null
    @Volatile private var ttsReady = false
    /** Segment end of the utterance whose reply is about to be spoken (latency log only). */
    @Volatile private var replySegmentEndMs: Long? = null

    /** Set by the guided screen: "Muse, start recording" / "Muse, stop recording". */
    @Volatile var onRecordingCommand: ((start: Boolean) -> Unit)? = null

    private fun refreshWakeNames() {
        val current = link
        scope.launch {
            wakeNames = try {
                current.wakeNames()
            } catch (_: Exception) {
                null
            } ?: WakeWord.Names.DEFAULT
        }
    }

    fun startListening() {
        refreshWakeNames()
        if (reader != null) return
        ensureTts()
        val thread = Thread({ readLoop() }, "anastylos-voice")
        reader = thread
        _state.update {
            it.copy(listening = true, status = "Listening for “Muse” …", listeningSinceMs = SystemClock.elapsedRealtime(), segments = 0)
        }
        thread.start()
    }

    fun stopListening() {
        reader = null
        armedUntilMs = 0L
        _state.update { it.copy(listening = false, pushToTalk = false, status = "", listeningSinceMs = null, heard = null) }
    }

    /** Push-to-talk fallback: start collecting regardless of the wake word. */
    fun pushToTalkDown() {
        if (!_state.value.listening) startListening()
        synchronized(this) { pttSamples = mutableListOf() }
        _state.update { it.copy(pushToTalk = true, status = "Recording … release to send") }
    }

    fun pushToTalkUp() {
        val blocks = synchronized(this) { pttSamples.also { pttSamples = null } } ?: return
        _state.update { it.copy(pushToTalk = false) }
        val samples = concat(blocks)
        if (samples.size < SpeechSegmenter.SAMPLE_RATE * 3 / 10) {
            _state.update { it.copy(status = "Too short. Hold while you speak.") }
            return
        }
        handleClip(Segment(++segmentCount, samples, SystemClock.elapsedRealtime()), wakeRequired = false)
    }

    /** Speaks workstation-verified text only (see [VoiceLink.SpokenReply]). */
    fun speakVerified(text: String) = speak(text)

    fun release() {
        stopListening()
        tts?.shutdown()
        tts = null
    }

    @SuppressLint("MissingPermission") // caller requests RECORD_AUDIO before startListening
    private fun readLoop() {
        val rate = SpeechSegmenter.SAMPLE_RATE
        val block = SpeechSegmenter.BLOCK_SAMPLES
        val min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val record = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION, rate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min, block * 2 * 4),
            )
        } catch (e: Exception) {
            fail("Microphone not available: ${e.message}")
            return
        }
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            fail("Microphone not available")
            return
        }
        val segmenter = SpeechSegmenter()
        val buffer = ShortArray(block)
        var blocks = 0L
        try {
            record.startRecording()
            Log.i(VOICE_LOG_TAG, "mic started rate=$rate state=${record.recordingState} source=VOICE_RECOGNITION")
            while (reader === Thread.currentThread()) {
                var filled = 0
                while (filled < block) {
                    val n = record.read(buffer, filled, block - filled)
                    if (n <= 0) break
                    filled += n
                }
                if (filled < block) continue
                blocks++
                synchronized(this) { pttSamples?.add(buffer.copyOf()) }
                val s = _state.value
                if (s.pushToTalk || s.speaking) {
                    segmenter.reset()
                    continue
                }
                val samples = segmenter.feed(buffer)
                if (blocks % 50 == 0L) _state.update { it.copy(backgroundRms = segmenter.backgroundRms) }
                if (samples != null) {
                    _state.update { it.copy(segments = it.segments + 1) }
                    val endMs = SystemClock.elapsedRealtime()
                    val segment = Segment(++segmentCount, samples, endMs)
                    val disposition = synchronized(this) {
                        when {
                            !_state.value.busy -> "processed"
                            held == null -> "held".also { held = segment }
                            else -> "dropped_busy"
                        }
                    }
                    Log.i(
                        VOICE_LOG_TAG,
                        "segment id=${segment.id} vad_start=${endMs - segment.durationMs} vad_end=$endMs " +
                            "ms=${segment.durationMs} bytes=${samples.size * 2} " +
                            "floor=${"%.4f".format(Locale.US, segmenter.backgroundRms)} disposition=$disposition",
                    )
                    if (disposition == "processed") handleClip(segment, wakeRequired = true)
                }
            }
        } finally {
            try {
                record.stop()
            } catch (_: Exception) {
            }
            record.release()
        }
    }

    private class Segment(val id: Long, val samples: ShortArray, val endMs: Long) {
        val durationMs: Long get() = samples.size * 1000L / SpeechSegmenter.SAMPLE_RATE
    }

    private fun handleClip(segment: Segment, wakeRequired: Boolean) {
        _state.update { it.copy(busy = true) }
        val spokeEndMs = segment.endMs
        var next: Segment? = null
        scope.launch {
            try {
                // A bare wake word arms exactly one following segment (TL point 5).
                val armed = wakeRequired && segment.endMs < armedUntilMs
                if (wakeRequired) armedUntilMs = 0L
                val wav = WavEncoder.encode(segment.samples)
                val transcribeStart = SystemClock.elapsedRealtime()
                val transcript = link.transcribe(wav)
                val transcribeMs = SystemClock.elapsedRealtime() - transcribeStart
                if (transcript.status != "ok") {
                    Log.i(VOICE_LOG_TAG, "segment id=${segment.id} transcribe status=${transcript.status} ms=$transcribeMs")
                    _state.update { it.copy(status = statusLine(transcript.status)) }
                    return@launch
                }
                // Nothing recognisable: discarded like unaddressed speech; an
                // earlier failure stays on screen instead of being wiped.
                if (transcript.text.isNullOrBlank()) {
                    Log.i(VOICE_LOG_TAG, "segment id=${segment.id} transcribe status=empty ms=$transcribeMs")
                    return@launch
                }
                val route = if (wakeRequired) {
                    WakeWord.route(transcript.text, armed = armed, names = wakeNames)
                } else {
                    WakeWord.route(transcript.text, armed = true, names = wakeNames)
                }
                Log.i(VOICE_LOG_TAG, "segment id=${segment.id} route=${route::class.simpleName} armed=$armed wakeRequired=$wakeRequired")
                when (route) {
                    WakeWord.Route.Discard -> return@launch
                    WakeWord.Route.Armed -> {
                        armedUntilMs = SystemClock.elapsedRealtime() + WakeWord.ARMED_MS
                        next = synchronized(this@VoiceController) { held.also { held = null } }
                        if (next != null) return@launch // already spoken: process it instead of asking
                        _state.update { it.copy(status = "Yes? (listening for your command)") }
                        speak(YES)
                        return@launch
                    }
                    is WakeWord.Route.Command -> {
                        showHeard(route.command.name.lowercase().replace('_', ' '))
                        val inputStart = SystemClock.elapsedRealtime()
                        val answer = link.submit(VoiceLink.OperatorInput.Command(route.command))
                        reply(answer, spokeEndMs, transcribeMs, SystemClock.elapsedRealtime() - inputStart)
                    }
                    is WakeWord.Route.Recording -> {
                        showHeard(if (route.start) "start recording" else "stop recording")
                        onRecordingCommand?.invoke(route.start)
                        speak(if (route.start) RECORDING_STARTED else RECORDING_STOPPED)
                    }
                    is WakeWord.Route.Input -> {
                        showHeard(route.text)
                        val inputStart = SystemClock.elapsedRealtime()
                        val answer = link.submit(VoiceLink.OperatorInput.Speech(route.text))
                        reply(answer, spokeEndMs, transcribeMs, SystemClock.elapsedRealtime() - inputStart)
                    }
                }
            } catch (e: Exception) {
                Log.w(VOICE_LOG_TAG, "voice failed ${e::class.java.simpleName}: ${e.message?.take(120)}")
                _state.update { it.copy(status = "Voice failed: ${e.message}") }
            } finally {
                val dropped = synchronized(this@VoiceController) { held.also { held = null } }
                if (dropped != null) Log.i(VOICE_LOG_TAG, "segment id=${dropped.id} disposition=dropped_not_after_wake")
                val follow = next
                if (follow != null) {
                    Log.i(VOICE_LOG_TAG, "segment id=${follow.id} disposition=processed_after_wake")
                    handleClip(follow, wakeRequired = true)
                } else {
                    _state.update { it.copy(busy = false) }
                }
            }
        }
    }

    /**
     * Addressed speech only (wake word or push-to-talk), on screen and
     * never logged or stored — the reference UI's "You said" line.
     */
    private fun showHeard(text: String) = _state.update { it.copy(heard = text) }

    private fun reply(reply: VoiceLink.SpokenReply, spokeEndMs: Long, transcribeMs: Long, inputMs: Long) {
        _state.update { it.copy(status = statusLine(reply.status)) }
        Log.i(
            VOICE_LOG_TAG,
            "reply status=${reply.status} transcribe ms=$transcribeMs input ms=$inputMs " +
                "end-of-speech to reply ms=${SystemClock.elapsedRealtime() - spokeEndMs}",
        )
        val text = reply.text ?: return
        replySegmentEndMs = spokeEndMs
        speak(text)
    }

    private fun statusLine(status: String): String = when (status) {
        "ok" -> ""
        DetachedVoiceLink.STATUS -> "Voice needs an attached workstation session"
        "not_bound" -> "Voice is not bound to an agent session on the workstation"
        else -> "Not understood ($status). Please try again or type."
    }

    private fun speak(text: String) {
        val engine = tts ?: return
        if (!ttsReady) return
        _state.update { it.copy(speaking = true) }
        engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, "anastylos-${SystemClock.elapsedRealtime()}")
    }

    private fun ensureTts() {
        if (tts != null) return
        tts = TextToSpeech(appContext) { status ->
            val engine = tts ?: return@TextToSpeech
            ttsReady = status == TextToSpeech.SUCCESS &&
                engine.setLanguage(Locale.US) >= TextToSpeech.LANG_AVAILABLE
            Log.i(VOICE_LOG_TAG, "tts ready=$ttsReady locale=en-US")
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {
                    val since = replySegmentEndMs ?: return
                    replySegmentEndMs = null
                    Log.i(VOICE_LOG_TAG, "tts start ms after segment end=${SystemClock.elapsedRealtime() - since}")
                }
                override fun onDone(utteranceId: String?) = _state.update { it.copy(speaking = false) }
                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) = _state.update { it.copy(speaking = false) }
            })
        }
    }

    private fun fail(message: String) {
        reader = null
        _state.update { it.copy(listening = false, status = message) }
    }

    private fun concat(blocks: List<ShortArray>): ShortArray {
        val out = ShortArray(blocks.sumOf { it.size })
        var offset = 0
        for (b in blocks) {
            b.copyInto(out, offset)
            offset += b.size
        }
        return out
    }

    private companion object {
        /** Fixed status sentence of the reference behaviour; never model text. */
        const val YES = "Yes?"
        const val RECORDING_STARTED = "Recording."
        const val RECORDING_STOPPED = "Recording stopped."
    }
}
