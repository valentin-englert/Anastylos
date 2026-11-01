package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.anastylos.capture.voice.VoiceLink
import org.anastylos.capture.voice.WakeWord
import org.junit.Test

class GuidedVoiceLinkTest {
    private val session = "6f1c2a7e-0d1b-4b8e-9a51-2f0c3d4e5a6b"
    private val sent = mutableListOf<HttpRequest>()
    private var replies = ArrayDeque<HttpResponse>()

    private fun link(token: String? = "tok") = GuidedVoiceLink(
        client = GuidedControlClient({ request -> sent += request; replies.removeFirst() }),
        sessionId = session, token = { token }, utcNow = { "2026-11-01T17:00:00Z" },
        ioDispatcher = kotlinx.coroutines.Dispatchers.Unconfined,
    )

    private fun reply(status: Int, json: String) = HttpResponse(status, json.toByteArray())

    @Test
    fun transcribePostsWavWithGuidedToken() = runTest {
        replies += reply(200, """{"status":"ok","transcript":"muse next question","latency_s":1.7}""")
        val result = link().transcribe(byteArrayOf(1, 2, 3))
        assertThat(result).isEqualTo(VoiceLink.Transcript("ok", "muse next question", 1.7))
        val request = sent.single()
        assertThat(request.path).isEqualTo("/v1/guided/sessions/$session/voice/transcribe")
        assertThat(request.headers["Authorization"]).isEqualTo("Bearer tok")
        assertThat(request.headers["Content-Type"]).isEqualTo("audio/wav")
    }

    @Test
    fun speechCarriesOpKeyOfTheLastQuestion() = runTest {
        val l = link()
        replies += reply(200, """{"status":"ok","speak":{"text":"What came out first?","kind":"question","basis_ref":"k1"}}""")
        replies += reply(200, """{"status":"ok","speak":{"text":"A screw. Right?","kind":"read_back","basis_ref":"k1"}}""")
        assertThat(l.submit(VoiceLink.OperatorInput.Command(WakeWord.FixedCommand.NEXT_QUESTION)).text)
            .isEqualTo("What came out first?")
        assertThat(l.submit(VoiceLink.OperatorInput.Speech("screw from the back shell")).text)
            .isEqualTo("A screw. Right?")
        val first = Json.parseToJsonElement(sent[0].body!!.decodeToString()).jsonObject
        assertThat(first["kind"]!!.jsonPrimitive.content).isEqualTo("command")
        assertThat(first["command"]!!.jsonPrimitive.content).isEqualTo("next_question")
        val second = Json.parseToJsonElement(sent[1].body!!.decodeToString()).jsonObject
        val input = second["operator_input"]!!.jsonObject
        assertThat(input["schema"]!!.jsonPrimitive.content).isEqualTo("anastylos-live-operator-input@1")
        assertThat(input["source"]!!.jsonPrimitive.content).isEqualTo("speech_transcript")
        assertThat(input["op_key"]!!.jsonPrimitive.content).isEqualTo("k1")
        assertThat(input["text"]!!.jsonPrimitive.content).isEqualTo("screw from the back shell")
    }

    @Test
    fun errorsBecomeStatusWithoutSpeech() = runTest {
        replies += reply(409, """{"error":{"code":"not_bound","message":"voice not bound"}}""")
        assertThat(link().submit(VoiceLink.OperatorInput.Speech("x"))).isEqualTo(VoiceLink.SpokenReply("not_bound", null))
        assertThat(link(token = null).transcribe(byteArrayOf())).isEqualTo(VoiceLink.Transcript("not_enrolled", null, null))
    }
}
