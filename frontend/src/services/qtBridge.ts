import type { BridgeResult } from '../types/bridge'

type QtSignal<T extends unknown[] = []> = {
  connect: (cb: (...args: T) => void) => void
  disconnect?: (cb: (...args: T) => void) => void
}

/** Qt WebChannel objects expose callable slots plus optional Signals. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type QtObject = Record<string, any> & {
  taskChanged?: QtSignal<[string]>
  closeCheckRequested?: QtSignal
}

declare global {
  interface Window {
    qt?: { webChannelTransport: unknown }
    QWebChannel?: new (
      transport: unknown,
      init: (channel: { objects: Record<string, QtObject> }) => void,
    ) => void
  }
}

export interface Bridges {
  application: QtObject
  github: QtObject
  knowledge: QtObject
  settings: QtObject
  tasks: QtObject
  webArchive: QtObject
}

let bridgesPromise: Promise<Bridges | null> | null = null

export function connectBridges(): Promise<Bridges | null> {
  if (bridgesPromise) return bridgesPromise
  bridgesPromise = new Promise((resolve) => {
    if (!window.qt || !window.QWebChannel) {
      resolve(null)
      return
    }
    new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
      const objects = channel.objects
      resolve({
        application: objects.application,
        github: objects.github,
        knowledge: objects.knowledge,
        settings: objects.settings,
        tasks: objects.tasks,
        webArchive: objects.webArchive,
      })
    })
  })
  return bridgesPromise
}

export async function callBridge<T>(
  invoker: (() => unknown) | undefined,
): Promise<BridgeResult<T>> {
  if (!invoker) {
    return { ok: false, error: { code: 'NO_BRIDGE', message: '桌面桥接不可用' } }
  }
  try {
    const raw = await Promise.resolve(invoker())
    if (typeof raw !== 'string') {
      return { ok: false, error: { code: 'BAD_PAYLOAD', message: '桥接返回格式错误' } }
    }
    return JSON.parse(raw) as BridgeResult<T>
  } catch (error) {
    return {
      ok: false,
      error: { code: 'BRIDGE_EXCEPTION', message: error instanceof Error ? error.message : String(error) },
    }
  }
}
