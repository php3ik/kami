import { useEffect, useMemo, useState } from 'react'
import { Check, EyeOff, Loader2, Save, Settings2, X } from 'lucide-react'
import * as api from '../api/client'

const providerModels: Record<string, { cheap: string; strong: string }> = {
  openai: { cheap: 'gpt-5.4-mini', strong: 'gpt-5.5' },
  anthropic: { cheap: 'claude-haiku-4-5-20251001', strong: 'claude-sonnet-4-6' },
  gemini: { cheap: 'gemini-2.5-flash', strong: 'gemini-2.5-pro' },
}

const imageProviderModels: Record<string, { cheap: string; strong: string }> = {
  openai: { cheap: 'gpt-image-1-mini', strong: 'gpt-image-2' },
  gemini: { cheap: 'imagen-4.0-fast-generate-001', strong: 'imagen-4.0-ultra-generate-001' },
}

interface Props {
  open: boolean
  onClose: () => void
}

export default function LLMSettingsModal({ open, onClose }: Props) {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [settings, setSettings] = useState<any | null>(null)
  const [form, setForm] = useState({
    provider: 'openai',
    cheap_model: '',
    strong_model: '',
    anthropic_api_key: '',
    openai_api_key: '',
    gemini_api_key: '',
    image_provider: 'openai',
    cheap_image_model: '',
    strong_image_model: '',
  })

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setError(null)
    setSaved(false)
    api.fetchLLMSettings()
      .then((data) => {
        setSettings(data)
        setForm((prev) => ({
          ...prev,
          provider: data.provider || 'openai',
          cheap_model: data.cheap_model || '',
          strong_model: data.strong_model || '',
          anthropic_api_key: '',
          openai_api_key: '',
          gemini_api_key: '',
          image_provider: data.image?.provider || data.provider || 'openai',
          cheap_image_model: data.image?.cheap_model || '',
          strong_image_model: data.image?.strong_model || '',
        }))
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load settings'))
      .finally(() => setLoading(false))
  }, [open])

  const activeToken = settings?.tokens?.[form.provider]
  const providerLabel = useMemo(() => form.provider.charAt(0).toUpperCase() + form.provider.slice(1), [form.provider])

  if (!open) return null

  const applyPreset = (provider: string) => {
    const preset = providerModels[provider]
    setForm((prev) => ({
      ...prev,
      provider,
      cheap_model: preset?.cheap || prev.cheap_model,
      strong_model: preset?.strong || prev.strong_model,
    }))
    setSaved(false)
  }

  const save = async () => {
    setSaving(true)
    setSaved(false)
    setError(null)
    try {
      const payload: any = {
        provider: form.provider,
        cheap_model: form.cheap_model.trim(),
        strong_model: form.strong_model.trim(),
        image_provider: form.image_provider,
        cheap_image_model: form.cheap_image_model.trim(),
        strong_image_model: form.strong_image_model.trim(),
      }
      if (form.anthropic_api_key.trim()) payload.anthropic_api_key = form.anthropic_api_key.trim()
      if (form.openai_api_key.trim()) payload.openai_api_key = form.openai_api_key.trim()
      if (form.gemini_api_key.trim()) payload.gemini_api_key = form.gemini_api_key.trim()
      const updated = await api.updateLLMSettings(payload)
      setSettings(updated)
      setForm((prev) => ({
        ...prev,
        anthropic_api_key: '',
        openai_api_key: '',
        gemini_api_key: '',
      }))
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  const applyImagePreset = (provider: string) => {
    const preset = imageProviderModels[provider]
    setForm((prev) => ({
      ...prev,
      image_provider: provider,
      cheap_image_model: preset?.cheap || prev.cheap_image_model,
      strong_image_model: preset?.strong || prev.strong_image_model,
    }))
    setSaved(false)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4">
      <div className="w-full max-w-3xl border border-slate-700 bg-slate-950 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 px-5 py-4">
          <div>
            <div className="flex items-center gap-2 text-lg font-semibold text-slate-100">
              <Settings2 size={18} />
              LLM Settings
            </div>
            <p className="mt-1 text-xs text-slate-500">Runtime provider, model tiers, and API token replacement.</p>
          </div>
          <button onClick={onClose} className="rounded p-2 text-slate-400 hover:bg-slate-900 hover:text-slate-100" title="Close">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-5 px-5 py-5">
          {loading ? (
            <div className="flex items-center gap-3 py-12 text-sm text-slate-400">
              <Loader2 size={18} className="animate-spin" />
              Loading settings...
            </div>
          ) : (
            <>
              {error && <div className="border border-rose-500/60 bg-rose-950/40 px-3 py-2 text-sm text-rose-100">{error}</div>}
              {saved && (
                <div className="flex items-center gap-2 border border-emerald-500/50 bg-emerald-950/30 px-3 py-2 text-sm text-emerald-100">
                  <Check size={15} />
                  Settings saved. Next LLM call will use the updated provider and models.
                </div>
              )}

              <section>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Provider</div>
                <div className="grid grid-cols-3 gap-2">
                  {['openai', 'anthropic', 'gemini'].map((provider) => (
                    <button
                      key={provider}
                      onClick={() => applyPreset(provider)}
                      className={`border px-3 py-3 text-left transition-colors ${
                        form.provider === provider
                          ? 'border-purple-400 bg-purple-950/60 text-white'
                          : 'border-slate-800 bg-slate-900/50 text-slate-300 hover:border-slate-600'
                      }`}
                    >
                      <div className="text-sm font-semibold capitalize">{provider}</div>
                      <div className="mt-1 flex items-center gap-1 text-xs text-slate-500">
                        <EyeOff size={12} />
                        {settings?.tokens?.[provider]?.configured ? settings.tokens[provider].masked : 'token missing'}
                      </div>
                    </button>
                  ))}
                </div>
              </section>

              <section className="grid gap-3 md:grid-cols-2">
                <label className="space-y-1">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Cheap model</span>
                  <input
                    value={form.cheap_model}
                    onChange={(e) => setForm((prev) => ({ ...prev, cheap_model: e.target.value }))}
                    className="w-full border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-purple-400"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Strong model</span>
                  <input
                    value={form.strong_model}
                    onChange={(e) => setForm((prev) => ({ ...prev, strong_model: e.target.value }))}
                    className="w-full border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-purple-400"
                  />
                </label>
              </section>

              <section>
                <div className="mb-2 flex items-center justify-between">
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">API tokens</div>
                  <div className="text-xs text-slate-500">
                    Active: {providerLabel} · {activeToken?.configured ? activeToken.masked : 'missing token'}
                  </div>
                </div>
                <div className="grid gap-3">
                  {[
                    ['openai_api_key', 'OpenAI API key'],
                    ['anthropic_api_key', 'Anthropic API key'],
                    ['gemini_api_key', 'Gemini API key'],
                  ].map(([key, label]) => (
                    <label key={key} className="space-y-1">
                      <span className="text-xs text-slate-400">{label}</span>
                      <input
                        type="password"
                        value={(form as any)[key]}
                        placeholder="Leave blank to keep current token"
                        onChange={(e) => setForm((prev) => ({ ...prev, [key]: e.target.value }))}
                        className="w-full border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-purple-400"
                      />
                    </label>
                  ))}
                </div>
              </section>

              <section className="border-t border-slate-800 pt-5">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Image generation</div>
                <div className="grid grid-cols-2 gap-2">
                  {['openai', 'gemini'].map((provider) => (
                    <button
                      key={provider}
                      onClick={() => applyImagePreset(provider)}
                      className={`border px-3 py-3 text-left transition-colors ${
                        form.image_provider === provider
                          ? 'border-cyan-400 bg-cyan-950/40 text-white'
                          : 'border-slate-800 bg-slate-900/50 text-slate-300 hover:border-slate-600'
                      }`}
                    >
                      <div className="text-sm font-semibold capitalize">{provider}</div>
                      <div className="mt-1 text-xs text-slate-500">World map and visual assets</div>
                    </button>
                  ))}
                </div>
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <label className="space-y-1">
                    <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Cheap image model</span>
                    <input
                      value={form.cheap_image_model}
                      onChange={(e) => setForm((prev) => ({ ...prev, cheap_image_model: e.target.value }))}
                      className="w-full border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Strong image model</span>
                    <input
                      value={form.strong_image_model}
                      onChange={(e) => setForm((prev) => ({ ...prev, strong_image_model: e.target.value }))}
                      className="w-full border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400"
                    />
                  </label>
                </div>
              </section>
            </>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-800 px-5 py-4">
          <button onClick={onClose} className="px-4 py-2 text-sm text-slate-400 hover:text-slate-100">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={saving || loading}
            className="inline-flex items-center gap-2 bg-purple-600 px-4 py-2 text-sm font-semibold text-white hover:bg-purple-500 disabled:bg-slate-800 disabled:text-slate-500"
          >
            {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
            Save Settings
          </button>
        </div>
      </div>
    </div>
  )
}
