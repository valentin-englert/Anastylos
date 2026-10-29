package org.anastylos.capture.sync

/**
 * One bound remote-control endpoint: the session driver plus the
 * delivery pipeline, sharing the caller's pinned transport. Built
 * per attach by [GuidedRemoteFactory] (the container closes over
 * the stores); torn down by detaching the session.
 */
data class GuidedRemote(
    val session: GuidedRemoteSession,
    val delivery: GuidedDelivery,
)

fun interface GuidedRemoteFactory {
    fun create(transport: HttpTransport): GuidedRemote
}
