package org.anastylos.capture.pairing

import com.google.common.truth.Truth.assertThat
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.net.InetAddress
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import java.time.Instant
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.runTest
import kotlin.time.Duration.Companion.seconds
import org.anastylos.capture.sync.HttpsTransport
import org.anastylos.capture.sync.QrPairPayload
import org.anastylos.capture.sync.pinnedSpkiSocketFactory
import org.anastylos.capture.sync.spkiSha256Bytes
import org.junit.After
import org.junit.Assume
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * The pairing client against a real TLS handshake.
 *
 * [FakeTransport]-style tests prove the protocol mapping; this suite
 * proves the transport pairing itself: the SPKI pin the client
 * carries matches the key a live server presents, through a genuine
 * `javax.net.ssl` handshake over loopback — the exact
 * [HttpsTransport] + [pinnedSpkiSocketFactory] path the app uses.
 * The server key comes from the JDK's own `keytool` (no new
 * dependencies); when `keytool` is unavailable the suite skips
 * instead of faking the handshake.
 */
class TlsRoundTripTest {
    @get:Rule
    val temp = TemporaryFolder()

    private var server: ScriptedTlsServer? = null

    @After
    fun stopServer() {
        server?.close()
        server = null
    }

    private fun keytool(): File {
        val exe = if (System.getProperty("os.name").lowercase().contains("win")) {
            "keytool.exe"
        } else {
            "keytool"
        }
        val bin = File(System.getProperty("java.home"), "bin/$exe")
        Assume.assumeTrue("keytool unavailable; cannot mint a server key", bin.isFile)
        return bin
    }

    private fun mintKeystore(): Triple<File, String, java.security.cert.X509Certificate> {
        val store = File(temp.root, "ws.p12")
        val password = "test-store-password"
        val process = ProcessBuilder(
            keytool().absolutePath,
            "-genkeypair",
            "-alias", "ws",
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "2",
            "-storetype", "PKCS12",
            "-keystore", store.absolutePath,
            "-storepass", password,
            "-keypass", password,
            "-dname", "CN=anastylos-test-workstation",
            "-ext", "SAN=ip:127.0.0.1,dns:localhost",
        ).redirectErrorStream(true).start()
        val finished = process.waitFor(60, TimeUnit.SECONDS)
        Assume.assumeTrue("keytool timed out minting the server key", finished)
        Assume.assumeTrue(
            "keytool failed: ${process.inputStream.readBytes().toString(Charsets.UTF_8).take(300)}",
            process.exitValue() == 0 && store.isFile,
        )
        val keys = KeyStore.getInstance("PKCS12")
        store.inputStream().use { keys.load(it, password.toCharArray()) }
        val cert = keys.getCertificate("ws") as java.security.cert.X509Certificate
        return Triple(store, password, cert)
    }

    /** Minimal scripted HTTPS/1.1 server: canned JSON per scripted step. */
    private class ScriptedTlsServer(
        keystore: File,
        password: String,
        private val steps: MutableList<Pair<Int, String>>,
    ) : AutoCloseable {
        val requests = mutableListOf<String>()
        private val socket: SSLServerSocket
        val port: Int
        private val running = AtomicBoolean(true)
        private val ready = CountDownLatch(1)
        private val thread: Thread

        init {
            val keys = KeyStore.getInstance("PKCS12")
            keystore.inputStream().use { keys.load(it, password.toCharArray()) }
            val managers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
            managers.init(keys, password.toCharArray())
            val context = SSLContext.getInstance("TLS")
            context.init(managers.keyManagers, null, null)
            socket = context.serverSocketFactory.createServerSocket(
                0, 50, InetAddress.getByName("127.0.0.1"),
            ) as SSLServerSocket
            port = socket.localPort
            thread = Thread(::acceptLoop, "tls-roundtrip-server").apply {
                isDaemon = true
                start()
            }
            check(ready.await(10, TimeUnit.SECONDS)) { "test server did not start" }
        }

        private fun acceptLoop() {
            ready.countDown()
            while (running.get()) {
                val connection = try {
                    socket.accept()
                } catch (_: Exception) {
                    return
                }
                try {
                    connection.soTimeout = 10_000
                    val text = BufferedReader(
                        InputStreamReader(connection.inputStream, StandardCharsets.UTF_8),
                    )
                    val head = StringBuilder()
                    var contentLength = 0
                    while (true) {
                        val line = text.readLine() ?: break
                        if (line.isEmpty()) break
                        head.append(line).append('\n')
                        if (line.startsWith("Content-Length:", ignoreCase = true)) {
                            contentLength = line.substringAfter(':').trim().toIntOrNull() ?: 0
                        }
                    }
                    if (contentLength > 0) {
                        val body = CharArray(contentLength)
                        var read = 0
                        while (read < contentLength) {
                            val n = text.read(body, read, contentLength - read)
                            if (n < 0) break
                            read += n
                        }
                        head.append(String(body, 0, read))
                    }
                    val firstLine = head.lines().firstOrNull().orEmpty()
                    synchronized(requests) { requests += firstLine }
                    val (status, json) = synchronized(steps) {
                        if (steps.isEmpty()) 500 to "{\"error\":{\"message\":\"script exhausted\"}}"
                        else steps.removeFirst()
                    }
                    val bytes = json.toByteArray(StandardCharsets.UTF_8)
                    val reply = "HTTP/1.1 $status ${reason(status)}\r\n" +
                        "Content-Type: application/json\r\n" +
                        "Content-Length: ${bytes.size}\r\n" +
                        "Connection: close\r\n\r\n"
                    connection.outputStream.write(reply.toByteArray(StandardCharsets.UTF_8))
                    connection.outputStream.write(bytes)
                    connection.outputStream.flush()
                } catch (_: Exception) {
                    // A failed test handshake is data, not a server crash.
                } finally {
                    try {
                        connection.close()
                    } catch (_: Exception) {
                    }
                }
            }
        }

        private fun reason(status: Int) = when (status) {
            200 -> "OK"
            202 -> "Accepted"
            404 -> "Not Found"
            else -> "Error"
        }

        override fun close() {
            running.set(false)
            try {
                socket.close()
            } catch (_: Exception) {
            }
            thread.join(5_000)
        }
    }

    private fun payload(port: Int, spki: ByteArray) = QrPairPayload(
        workstationId = "test-workstation",
        baseUrl = "https://127.0.0.1:$port",
        host = "127.0.0.1",
        port = port,
        spkiSha256 = spki,
        pairingCode = "anchor banana cherry desert eagle frost",
        expiresAt = Instant.now().plusSeconds(300),
    )

    private fun client() = HttpQrPairClient(
        transportFactory = { qr ->
            HttpsTransport(qr.baseUrl, pinnedSpkiSocketFactory(qr.spkiSha256))
        },
        // Real blocking sockets: real IO dispatcher, short poll beats.
        ioDispatcher = Dispatchers.IO,
        pollIntervalMillis = 50,
    )

    @Test
    fun `redeem poll and verify complete over a pinned handshake`() = runTest(timeout = 120.seconds) {
        val (keystore, password, cert) = mintKeystore()
        server = ScriptedTlsServer(
            keystore, password, mutableListOf(
                202 to "{\"status\":\"pending\",\"request_id\":\"${"a".repeat(32)}\",\"expires_at\":1999999999}",
                200 to "{\"status\":\"pending\"}",
                200 to "{\"status\":\"approved\",\"workstation_id\":\"ws-live\",\"token\":\"tok-live\"}",
                200 to "{\"workstation_id\":\"ws-live\",\"staged_packages\":0}",
            ),
        )
        val live = server!!
        val qr = payload(live.port, spkiSha256Bytes(cert))
        val pair = client()

        val redeemed = pair.redeem(qr, "Test Phone")
        assertThat(redeemed).isInstanceOf(RedeemOutcome.Pending::class.java)
        val request = (redeemed as RedeemOutcome.Pending).request
        val approved = pair.awaitApproval(qr, request)
        assertThat(approved).isEqualTo(ApprovalOutcome.Approved("tok-live", "ws-live"))
        val verified = pair.verify(qr, "tok-live")
        assertThat(verified).isEqualTo(VerifyOutcome.Verified("ws-live"))

        assertThat(live.requests).containsExactly(
            "POST /v1/pair/qr HTTP/1.1",
            "GET /v1/pair/qr/${"a".repeat(32)} HTTP/1.1",
            "GET /v1/pair/qr/${"a".repeat(32)} HTTP/1.1",
            "GET /v1/status HTTP/1.1",
        ).inOrder()
    }

    @Test
    fun `wrong pin fails the handshake and classifies as mismatch`() =
        runTest(timeout = 120.seconds) {
            val (keystore, password, _) = mintKeystore()
            server = ScriptedTlsServer(keystore, password, mutableListOf())
            val live = server!!
            // A pin for a key the server does not hold.
            val qr = payload(live.port, ByteArray(32) { 0x7F })
            val outcome = client().redeem(qr, "Test Phone")
            assertThat(outcome).isInstanceOf(RedeemOutcome.PinMismatch::class.java)
            // The handshake died before any HTTP was spoken.
            assertThat(live.requests).isEmpty()
        }

}
