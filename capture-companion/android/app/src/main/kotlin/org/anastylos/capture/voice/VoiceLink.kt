package org.anastylos.capture.voice

/**
 * Phone ↔ workstation seam for voice. The phone never holds a model key:
 * it sends a WAV clip and operator input, the workstation transcribes with
 * muse-voice-transcribe-1.0 and returns the only text the phone may speak
 * (L0 questions, read-backs, fixed status sentences, verified guide
 * answers). It must travel over the paired, encrypted receiver/control
 * channel (TL decision R9/R10, `GuidedVoiceLink`). Without an attached
 * workstation session the link is [DetachedVoiceLink], which sends
 * nothing (docs/integration/VOICE-01.md).
 */
interface VoiceLink {
    suspend fun transcribe(wav: ByteArray): Transcript

    /** Transcript or fixed command as operator input; returns text to speak. */
    suspend fun submit(input: OperatorInput): SpokenReply

    data class Transcript(val status: String, val text: String?, val latencyS: Double?)

    sealed interface OperatorInput {
        /** anastylos-live-operator-input@1, source "speech_transcript". */
        data class Speech(val text: String) : OperatorInput
        data class Command(val command: WakeWord.FixedCommand) : OperatorInput
    }

    /** [text] is workstation-verified; null when there is nothing to say. */
    data class SpokenReply(val status: String, val text: String?)
}

/** No attached workstation session: every call reports it, nothing is sent. */
class DetachedVoiceLink : VoiceLink {
    override suspend fun transcribe(wav: ByteArray) =
        VoiceLink.Transcript(STATUS, null, null)

    override suspend fun submit(input: VoiceLink.OperatorInput) =
        VoiceLink.SpokenReply(STATUS, null)

    companion object {
        const val STATUS = "detached"
    }
}
