import { useEffect, useMemo, useState } from 'react'

type LoginState = {
  username: string
  password: string
  matricula: string
  loginUrl: string
  verifySsl: boolean
  rememberMe: boolean
}

type Period = {
  key: string
  label: string
  year: number
  semester: number
  subjects: Subject[]
}

type Subject = {
  name: string
  noteUrl: string | null
  absences: string | null
}

type SessionStatus = {
  authenticated: boolean
  matricula: string | null
}

const defaultLogin: LoginState = {
  username: '',
  password: '',
  matricula: '',
  loginUrl: 'https://portalaluno.ufcspa.edu.br/aluno/login.action?error=',
  verifySsl: false,
  rememberMe: true,
}

const configuredApiBaseUrl =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''
const browserHostname = typeof window !== 'undefined' ? window.location.hostname : ''
const apiBaseUrl = browserHostname.endsWith('trycloudflare.com') ? '' : configuredApiBaseUrl

function apiUrl(path: string) {
  return apiBaseUrl ? `${apiBaseUrl}${path}` : path
}

function wait(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

export default function App() {
  const [loginForm, setLoginForm] = useState<LoginState>(defaultLogin)
  const [theme, setTheme] = useState<'light' | 'dark'>('light')
  const [authenticated, setAuthenticated] = useState(false)
  const [periods, setPeriods] = useState<Period[]>([])
  const [selectedPeriodKey, setSelectedPeriodKey] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const [visibleSubjects, setVisibleSubjects] = useState<string[]>([])
  const [checkingSession, setCheckingSession] = useState(true)
  const [preparingDashboard, setPreparingDashboard] = useState(false)
  const [loggingIn, setLoggingIn] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const isLoadingPortal = loggingIn || syncing || preparingDashboard

  useEffect(() => {
    void loadSession()
  }, [])

  useEffect(() => {
    const savedTheme = window.localStorage.getItem('theme')
    if (savedTheme === 'dark' || savedTheme === 'light') {
      setTheme(savedTheme)
    }
  }, [])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    window.localStorage.setItem('theme', theme)
  }, [theme])

  const selectedPeriod = useMemo(
    () => periods.find((period) => period.key === selectedPeriodKey) ?? periods[0] ?? null,
    [periods, selectedPeriodKey],
  )
  const filteredSubjects = useMemo(() => {
    const subjects = selectedPeriod?.subjects ?? []
    const normalizedSearch = searchTerm.trim().toLowerCase()
    const hasSearch = normalizedSearch.length > 0
    const hasSelection = visibleSubjects.length > 0

    return subjects.filter((subject) => {
      const matchesSearch = hasSearch
        ? subject.name.toLowerCase().includes(normalizedSearch)
        : true
      const matchesSelection = hasSelection ? visibleSubjects.includes(subject.name) : true

      if (!hasSearch && !hasSelection) {
        return false
      }

      return matchesSearch && matchesSelection
    })
  }, [searchTerm, selectedPeriod, visibleSubjects])
  const absenceSummary = useMemo(() => {
    const numericAbsences = filteredSubjects
      .map((subject) => Number(subject.absences))
      .filter((value) => Number.isFinite(value))

    return {
      subjectsWithData: numericAbsences.length,
      totalAbsences: numericAbsences.reduce((sum, value) => sum + value, 0),
    }
  }, [filteredSubjects])

  function getDefaultVisibleSubjects(nextPeriods: Period[], periodKey: string) {
    const period = nextPeriods.find((item) => item.key === periodKey) ?? nextPeriods[0] ?? null
    return period?.subjects.map((subject) => subject.name) ?? []
  }

  async function syncPortal() {
    let lastError: Error | null = null

    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const response = await fetch(apiUrl('/api/sync'), {
          method: 'POST',
          credentials: 'include',
        })
        const data = await response.json()
        if (!response.ok) {
          throw new Error(data.message || 'Falha ao carregar periodos.')
        }

        const nextPeriods = (data.periods ?? []) as Period[]
        if (!nextPeriods.length) {
          throw new Error('Nao foi possivel identificar os periodos na pagina de notas.')
        }

        const nextSelectedPeriodKey = nextPeriods.some((period) => period.key === selectedPeriodKey)
          ? selectedPeriodKey
          : (nextPeriods[0]?.key ?? '')

        setPeriods(nextPeriods)
        setSelectedPeriodKey(nextSelectedPeriodKey)
        setSearchTerm('')
        setVisibleSubjects(getDefaultVisibleSubjects(nextPeriods, nextSelectedPeriodKey))
        setMessage(`${nextPeriods.length} periodo(s) carregados.`)

        return nextPeriods
      } catch (error) {
        lastError =
          error instanceof Error ? error : new Error('Falha ao carregar periodos.')

        if (attempt === 0) {
          await wait(1200)
          continue
        }
      }
    }

    throw lastError ?? new Error('Falha ao carregar periodos.')
  }

  async function loadSession() {
    try {
      setCheckingSession(true)
      const response = await fetch(apiUrl('/api/session'), { credentials: 'include' })
      const data = (await response.json()) as SessionStatus
      if (data.matricula) {
        setLoginForm((current) => ({ ...current, matricula: data.matricula ?? '' }))
      }
      if (data.authenticated) {
        setPreparingDashboard(true)
        await syncPortal()
        setAuthenticated(true)
      }
    } catch (sessionError) {
      setError(
        sessionError instanceof Error
          ? sessionError.message
          : 'Nao foi possivel verificar a sessao atual.',
      )
    } finally {
      setPreparingDashboard(false)
      setCheckingSession(false)
    }
  }

  async function handleLogin() {
    setPreparingDashboard(true)
    setLoggingIn(true)
    setError(null)
    setMessage(null)

    try {
      const response = await fetch(apiUrl('/api/login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(loginForm),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.message || 'Falha ao entrar no portal.')
      }

      setLoginForm((current) => ({ ...current, password: '' }))
      setMessage('Login realizado com sucesso. Carregando os dados do portal...')
      await syncPortal()
      setAuthenticated(true)
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : 'Falha ao entrar no portal.')
    } finally {
      setLoggingIn(false)
      setPreparingDashboard(false)
    }
  }

  async function handleLogout() {
    setError(null)
    setMessage(null)

    try {
      await fetch(apiUrl('/api/logout'), {
        method: 'POST',
        credentials: 'include',
      })
    } finally {
      setAuthenticated(false)
      setPeriods([])
      setSelectedPeriodKey('')
      setSearchTerm('')
      setVisibleSubjects([])
      setLoginForm((current) => ({ ...current, password: '' }))
    }
  }

  async function handleSync() {
    setSyncing(true)
    setError(null)

    try {
      await syncPortal()
    } catch (syncError) {
      setError(syncError instanceof Error ? syncError.message : 'Falha ao carregar periodos.')
    } finally {
      setSyncing(false)
    }
  }

  if (checkingSession || preparingDashboard) {
    const loadingTitle = checkingSession
      ? 'Verificando sessao...'
      : 'Entrando no portal e carregando suas disciplinas...'
    const loadingText = checkingSession
      ? 'Estamos conferindo se ja existe uma sessao valida para abrir o dashboard.'
      : 'Isso pode levar alguns segundos, principalmente no celular ou quando o backend acabou de acordar no Render.'

    return (
      <main className="login-shell">
        <section className="login-card loading-screen">
          <div className="loading-state loading-state-full" aria-live="polite">
            <div className="loading-spinner" />
            <div>
              <strong>{loadingTitle}</strong>
              <p className="empty-state">{loadingText}</p>
            </div>
          </div>
        </section>
      </main>
    )
  }

  if (!authenticated) {
    return (
      <main className="login-shell">
        <section className="login-card">
          <div className="login-copy">
            <p className="eyebrow">Portal UFCSPA</p>
            <h1>Entre para abrir um painel fofinho das materias.</h1>
            <p className="hero-copy">
              O login acontece no backend e a sessao do portal fica guardada no
              servidor. O navegador nao precisa armazenar a senha.
            </p>
          </div>

          <div className="login-form">
            <button
              className="ghost-button theme-toggle"
              type="button"
              onClick={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}
            >
              {theme === 'light' ? 'Tema escuro' : 'Tema claro'}
            </button>

            <label>
              Usuario
              <input
                value={loginForm.username}
                onChange={(event) =>
                  setLoginForm({ ...loginForm, username: event.target.value })
                }
                placeholder="Usuario institucional"
              />
            </label>

            <label>
              Senha
              <input
                type="password"
                value={loginForm.password}
                onChange={(event) =>
                  setLoginForm({ ...loginForm, password: event.target.value })
                }
                placeholder="Senha institucional"
              />
            </label>

            <label>
              Matricula
              <input
                value={loginForm.matricula}
                onChange={(event) =>
                  setLoginForm({ ...loginForm, matricula: event.target.value })
                }
                placeholder="Ex.: 527868"
              />
            </label>

            <label>
              URL de login
              <input
                value={loginForm.loginUrl}
                onChange={(event) =>
                  setLoginForm({ ...loginForm, loginUrl: event.target.value })
                }
              />
            </label>

            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={loginForm.verifySsl}
                onChange={(event) =>
                  setLoginForm({ ...loginForm, verifySsl: event.target.checked })
                }
              />
              Verificar certificado SSL
            </label>

            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={loginForm.rememberMe}
                onChange={(event) =>
                  setLoginForm({ ...loginForm, rememberMe: event.target.checked })
                }
              />
              Manter conectado
            </label>

            {error ? <p className="feedback error">{error}</p> : null}

            <button
              className="secondary-button"
              type="button"
              onClick={() => void handleLogin()}
              disabled={loggingIn}
            >
              {loggingIn ? 'Entrando...' : 'Entrar'}
            </button>
          </div>
        </section>
      </main>
    )
  }

  return (
    <main className="page-shell">
      <section className="dashboard-topbar">
        <div>
          <p className="eyebrow">Dashboard</p>
          <h1 className="dashboard-title">Suas materias</h1>
        </div>

        <div className="dashboard-actions">
          <button
            className="ghost-button"
            type="button"
            onClick={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}
          >
            {theme === 'light' ? 'Tema escuro' : 'Tema claro'}
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={() => void handleSync()}
            disabled={syncing}
          >
            {syncing ? 'Buscando no portal...' : 'Atualizar'}
          </button>
          <button className="ghost-button" type="button" onClick={() => void handleLogout()}>
            Sair
          </button>
        </div>
      </section>

      <section className="dashboard-grid">
        <article className="panel dashboard-panel">
          <p className="panel-kicker">Periodo</p>
          <h2>Semestre selecionado</h2>
          <label>
            Escolha um periodo
            <select
              value={selectedPeriod?.key ?? ''}
              onChange={(event) => {
                const nextPeriodKey = event.target.value
                setSelectedPeriodKey(nextPeriodKey)
                setSearchTerm('')
                setVisibleSubjects(getDefaultVisibleSubjects(periods, nextPeriodKey))
              }}
              disabled={periods.length === 0}
            >
              {periods.length === 0 ? (
                <option value="">Nenhum periodo encontrado</option>
              ) : (
                periods.map((period) => (
                  <option key={period.key} value={period.key}>
                    {period.label}
                  </option>
                ))
              )}
            </select>
          </label>
          <p className="hint">
            {selectedPeriod
              ? `${selectedPeriod.subjects.length} disciplina(s) neste periodo. Use a busca ou a selecao abaixo para mostrar as materias.`
              : 'Atualize o portal para carregar os periodos.'}
          </p>
        </article>

        <article className="panel dashboard-panel">
          <p className="panel-kicker">Resumo</p>
          <h2>Visao geral</h2>
          <div className="summary-inline">
            <div className="summary-pill">
              <span>Periodos</span>
              <strong>{periods.length}</strong>
            </div>
            <div className="summary-pill">
              <span>Materias visiveis</span>
              <strong>{filteredSubjects.length}</strong>
            </div>
            <div className="summary-pill">
              <span>Total de faltas</span>
              <strong>{absenceSummary.totalAbsences}</strong>
            </div>
          </div>
          {isLoadingPortal ? (
            <div className="loading-card" aria-live="polite">
              <div className="loading-dot" />
              <div>
                <strong>Buscando dados no portal...</strong>
                <p>
                  Isso pode levar alguns segundos enquanto o sistema entra no portal e carrega
                  as disciplinas.
                </p>
              </div>
            </div>
          ) : null}
          {message ? <p className="feedback success">{message}</p> : null}
          {error ? <p className="feedback error">{error}</p> : null}
        </article>
      </section>

      <section className="panel dashboard-panel">
        <div className="panel-header">
          <div>
            <p className="panel-kicker">Filtros</p>
            <h2>Filtrar disciplinas</h2>
          </div>
        </div>

        <div className="filters-toolbar">
          <label className="filter-card">
            Pesquisar por nome
            <input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Digite parte do nome da disciplina"
            />
          </label>

          <details className="selector-box" open={visibleSubjects.length > 0}>
            <summary className="selector-summary">
              <span>
                {visibleSubjects.length === 0
                  ? 'Nenhuma disciplina selecionada'
                  : `${visibleSubjects.length} disciplina(s) selecionada(s)`}
              </span>
            </summary>

            {!selectedPeriod ? (
              <p className="hint">Escolha um periodo para listar as disciplinas.</p>
            ) : (
              <>
                <div className="selector-actions">
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={() => setVisibleSubjects(selectedPeriod.subjects.map((subject) => subject.name))}
                  >
                    Marcar todas
                  </button>
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={() => setVisibleSubjects([])}
                  >
                    Limpar materias
                  </button>
                </div>

                <div className="selector-list">
                  {selectedPeriod.subjects.map((subject) => {
                    const checked = visibleSubjects.includes(subject.name)

                    return (
                      <label className="checkbox-row subject-toggle" key={subject.name}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(event) => {
                            setVisibleSubjects((current) => {
                              if (event.target.checked) {
                                return [...new Set([...current, subject.name])]
                              }

                              return current.filter((name) => name !== subject.name)
                            })
                          }}
                        />
                        {subject.name}
                      </label>
                    )
                  })}
                </div>
              </>
            )}
          </details>
        </div>
      </section>

      <section className="panel dashboard-panel">
        <div className="panel-header">
          <div>
            <p className="panel-kicker">Materias</p>
            <h2>{selectedPeriod?.label ?? 'Nenhum periodo selecionado'}</h2>
          </div>
        </div>

        {isLoadingPortal ? (
          <div className="loading-state" aria-live="polite">
            <div className="loading-spinner" />
            <div>
              <strong>Carregando disciplinas...</strong>
              <p className="empty-state">
                O backend ainda esta consultando o portal da universidade.
              </p>
            </div>
          </div>
        ) : !selectedPeriod ? (
          <p className="empty-state">Nenhum periodo carregado ainda.</p>
        ) : filteredSubjects.length === 0 ? (
          <p className="empty-state">
            Nenhuma disciplina encontrada com esse filtro. Ajuste a busca ou use a selecao de materias.
          </p>
        ) : (
          <div className="subject-list">
            {filteredSubjects.map((subject) => (
              <article className="subject-card" key={`${selectedPeriod.key}-${subject.name}`}>
                <div className="subject-card-top">
                  <div>
                    <h3>{subject.name}</h3>
                    <p>{selectedPeriod.label}</p>
                  </div>
                </div>

                <div className="subject-details">
                  <div className="subject-meta">
                    <span>
                      <strong>Faltas:</strong> {subject.absences ?? 'Nao encontrado'}
                    </span>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}
