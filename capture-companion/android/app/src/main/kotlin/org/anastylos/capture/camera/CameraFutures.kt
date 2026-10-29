package org.anastylos.capture.camera

import androidx.camera.lifecycle.ProcessCameraProvider
import com.google.common.util.concurrent.ListenableFuture
import java.util.concurrent.CancellationException as JucCancellationException
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executor
import kotlinx.coroutines.CancellationException as CoroutineCancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Source of the CameraX provider future. The default implementation calls
 * `ProcessCameraProvider.getInstance(context)`; tests inject a fake future
 * (delayed, failing, cancelled) without any camera hardware.
 */
fun interface CameraProviderSource {
    fun providerFuture(): ListenableFuture<ProcessCameraProvider>
}

/** Runs the command on the completing thread; never parks a thread. */
internal val DirectExecutor: Executor = Executor { command -> command.run() }

/**
 * Suspends until [future] completes without blocking any thread. The
 * `get()` below only runs inside the completion listener, where the
 * future is already done, so it returns immediately — the same shape as
 * the in-repo QR scanner listener, lifted into a cancellable suspend.
 *
 * Cancelling the awaiting coroutine cancels the future. A future that
 * fails on its own surfaces its cause; one cancelled elsewhere surfaces
 * coroutine cancellation.
 */
suspend fun <T> awaitListenableFuture(
    future: ListenableFuture<T>,
    listenerExecutor: Executor,
): T = suspendCancellableCoroutine { cont ->
    future.addListener(
        Runnable {
            if (cont.isCompleted) return@Runnable
            try {
                cont.resume(future.get())
            } catch (e: ExecutionException) {
                cont.resumeWithException(e.cause ?: e)
            } catch (e: JucCancellationException) {
                cont.resumeWithException(
                    CoroutineCancellationException("provider future was cancelled").also {
                        it.initCause(e)
                    },
                )
            } catch (e: InterruptedException) {
                cont.resumeWithException(
                    CoroutineCancellationException("interrupted awaiting provider").also {
                        it.initCause(e)
                    },
                )
            }
        },
        listenerExecutor,
    )
    cont.invokeOnCancellation { future.cancel(true) }
}

/**
 * Acquires the CameraX provider without blocking. Returns null when the
 * provider future cannot be created or fails (no backend, init error,
 * externally cancelled future). Rethrows only genuine coroutine
 * cancellation so structured concurrency still works.
 */
internal suspend fun acquireProviderOrNull(
    source: CameraProviderSource,
    listenerExecutor: Executor = DirectExecutor,
): ProcessCameraProvider? {
    val future = try {
        source.providerFuture()
    } catch (e: Exception) {
        return null
    }
    return try {
        awaitListenableFuture(future, listenerExecutor)
    } catch (e: CoroutineCancellationException) {
        // Our own coroutine was cancelled: propagate. Otherwise the
        // future died on its own and that is a plain bind failure.
        if (!currentCoroutineContext().isActive) throw e
        null
    } catch (e: Exception) {
        null
    }
}
