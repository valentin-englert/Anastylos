package org.anastylos.capture.sync

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import org.anastylos.capture.voice.VoiceLink
import org.anastylos.capture.voice.WakeWord

/**
 * [VoiceLink] over the paired receiver (VOICE-01, TL decision R9/R10)
 * via [GuidedControlClient.voiceTranscribe] / [GuidedControlClient.voiceInput]
 * with the guided Bearer token of the attached session. The phone sends no names or paths; the workstation
 * binds the session and returns the only text the phone may speak.
 */
class GuidedVoiceLink(
    private val client: GuidedControlClient,
    private val sessionId: String,
    private val token: suspend () -> String?,
    private val utcNow: () -> String,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : VoiceLink {
    /** basis_ref of the last spoken question or read-back: the op_key of the answer. */
    @Volatile private var lastBasisRef: String = ""

    /** ASR keywords from the workstation's settings (the agent's name); sent with every transcription. */
    @Volatile private var keywords: List<String> = emptyList()

    override suspend fun transcribe(wav: ByteArray): VoiceLink.Transcript = withContext(ioDispatcher) {
        val bearer = token() ?: return@withContext VoiceLink.Transcript("not_enrolled", null, null)
        val response = try {
            client.voiceTranscribe(bearer, sessionId, wav, keywords)
        } catch (_: TransportException) {
            return@withContext VoiceLink.Transcript("network_error", null, null)
        }
        if (response.status != 200) return@withContext VoiceLink.Transcript(errorCode(response), null, null)
        val parsed = try {
            VoiceJson.decodeFromString(TranscriptBody.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext VoiceLink.Transcript("invalid_response", null, null)
        }
        VoiceLink.Transcript(parsed.status ?: "invalid_response", parsed.transcript, parsed.latencyS)
    }

    override suspend fun wakeNames(): WakeWord.Names? = withContext(ioDispatcher) {
        keywords = emptyList()
        val bearer = token() ?: return@withContext null
        val response = try {
            client.voiceSettings(bearer, sessionId)
        } catch (_: TransportException) {
            return@withContext null
        }
        if (response.status != 200) return@withContext null
        val parsed = try {
            VoiceJson.decodeFromString(SettingsBody.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext null
        }
        // Without a name the workstation keeps "Muse" and "Meta"; the phone does the same.
        if (parsed.agentName.isNullOrBlank()) return@withContext null
        keywords = parsed.asrKeywords
        WakeWord.Names.of(parsed.wakeWords + parsed.mishearings, parsed.agentName.orEmpty())
    }

    override suspend fun submit(input: VoiceLink.OperatorInput): VoiceLink.SpokenReply = withContext(ioDispatcher) {
        val bearer = token() ?: return@withContext VoiceLink.SpokenReply("not_enrolled", null)
        val body = when (input) {
            is VoiceLink.OperatorInput.Command -> buildJsonObject {
                put("kind", "command")
                put(
                    "command",
                    when (input.command) {
                        WakeWord.FixedCommand.NEXT_QUESTION -> "next_question"
                        WakeWord.FixedCommand.REPEAT -> "repeat"
                    },
                )
            }
            is VoiceLink.OperatorInput.Speech -> buildJsonObject {
                put("kind", "input")
                putJsonObject("operator_input") {
                    put("schema", "anastylos-live-operator-input@1")
                    put("op_key", lastBasisRef)
                    put("source", "speech_transcript")
                    put("text", input.text.take(MAX_TEXT))
                    put("received_at", utcNow())
                }
            }
        }
        val response = try {
            client.voiceInput(bearer, sessionId, body.toString())
        } catch (_: TransportException) {
            return@withContext VoiceLink.SpokenReply("network_error", null)
        }
        if (response.status != 200) return@withContext VoiceLink.SpokenReply(errorCode(response), null)
        val parsed = try {
            VoiceJson.decodeFromString(InputBody.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext VoiceLink.SpokenReply("invalid_response", null)
        }
        val speak = parsed.speak
        if (speak?.kind == "question" || speak?.kind == "read_back") lastBasisRef = speak.basisRef ?: ""
        VoiceLink.SpokenReply(parsed.status ?: "invalid_response", speak?.text?.takeIf { it.isNotBlank() })
    }

    private fun errorCode(response: HttpResponse): String = try {
        VoiceJson.decodeFromString(ErrorEnvelope.serializer(), response.bodyText()).error?.code
            ?: "http_${response.status}"
    } catch (_: Exception) {
        "http_${response.status}"
    }

    @Serializable
    private data class SettingsBody(
        @SerialName("agent_name") val agentName: String? = null,
        @SerialName("wake_words") val wakeWords: List<String> = emptyList(),
        @SerialName("asr_keywords") val asrKeywords: List<String> = emptyList(),
        val mishearings: List<String> = emptyList(),
    )

    @Serializable
    private data class TranscriptBody(
        val status: String? = null,
        val transcript: String? = null,
        @SerialName("latency_s") val latencyS: Double? = null,
    )

    @Serializable
    private data class Speak(
        val text: String? = null,
        val kind: String? = null,
        @SerialName("basis_ref") val basisRef: String? = null,
    )

    @Serializable
    private data class InputBody(val status: String? = null, val speak: Speak? = null)

    @Serializable
    private data class ErrorCode(val code: String? = null)

    @Serializable
    private data class ErrorEnvelope(val error: ErrorCode? = null)

    private companion object {
        val VoiceJson = Json { ignoreUnknownKeys = true }
        /** anastylos-live-operator-input@1 `text` maxLength. */
        const val MAX_TEXT = 2000
    }
}
