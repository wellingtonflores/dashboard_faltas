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
  displayName?: string
  subjectCode?: string | null
  noteUrl: string | null
  absences: string | null
  portalAbsences?: number | null
  manualAbsences?: number | null
  trackedAbsences?: number | null
  configuredHours?: number | null
  configuredPeriods?: number | null
  maxAbsences?: number | null
  manualMaxAbsences?: number | null
  remainingAbsences?: number | null
  maxAbsencesSource?: 'manual' | 'default' | 'pending'
  riskLevel?: 'healthy' | 'attention' | 'limit' | 'exceeded' | 'pending' | 'neutral'
  riskLabel?: string
  riskMessage?: string
  history?: Array<{
    id: string
    manualAbsences?: number | null
    maxAbsences?: number | null
    configuredHours?: number | null
    createdAt: string
  }>
}

type SessionStatus = {
  authenticated: boolean
  matricula: string | null
}

type Notice = {
  type: 'success' | 'error' | 'info'
  text: string
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

function extractInteger(value: string | number | null | undefined) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  if (value == null) {
    return null
  }

  const match = String(value).match(/-?\d+/)
  return match ? Number(match[0]) : null
}

function riskForSubject(
  trackedAbsences: number | null,
  maxAbsences: number | null,
): Pick<Subject, 'riskLevel' | 'riskLabel' | 'riskMessage'> {
  if (maxAbsences == null) {
    return {
      riskLevel: 'pending',
      riskLabel: 'Limite pendente',
      riskMessage: 'Configure a carga horaria ou o limite dessa materia.',
    }
  }

  if (trackedAbsences == null) {
    return {
      riskLevel: 'neutral',
      riskLabel: 'Sem faltas anotadas',
      riskMessage: 'Ainda nao ha faltas registradas manualmente nem encontradas no portal.',
    }
  }

  const remaining = maxAbsences - trackedAbsences
  if (remaining < 0) {
    return {
      riskLevel: 'exceeded',
      riskLabel: 'Limite ultrapassado',
      riskMessage: 'As faltas atuais ja passaram do limite configurado.',
    }
  }

  if (remaining === 0) {
    return {
      riskLevel: 'limit',
      riskLabel: 'No limite',
      riskMessage: 'Nao ha mais margem de faltas para essa disciplina.',
    }
  }

  if (remaining <= 2) {
    return {
      riskLevel: 'attention',
      riskLabel: 'Atencao',
      riskMessage: 'Restam poucas faltas disponiveis nessa disciplina.',
    }
  }

  return {
    riskLevel: 'healthy',
    riskLabel: 'Sob controle',
    riskMessage: 'A disciplina ainda esta com margem confortavel de faltas.',
  }
}

function deriveSubject(subject: Subject): Subject {
  const portalAbsences = subject.portalAbsences ?? extractInteger(subject.absences)
  const manualAbsences = subject.manualAbsences ?? null
  const trackedAbsences = manualAbsences ?? portalAbsences ?? null
  const configuredHours = subject.configuredHours ?? null
  const configuredPeriods =
    subject.configuredPeriods ??
    (configuredHours != null ? Math.floor((configuredHours * 60) / 50) : null)
  const inferredMaxAbsences =
    configuredPeriods != null ? Math.floor(configuredPeriods * 0.25) : null
  const manualMaxAbsences =
    subject.manualMaxAbsences ??
    (subject.maxAbsencesSource === 'manual' ? subject.maxAbsences ?? null : null)
  const maxAbsences = manualMaxAbsences ?? inferredMaxAbsences
  const remainingAbsences =
    maxAbsences != null && trackedAbsences != null ? maxAbsences - trackedAbsences : null
  const riskState = riskForSubject(trackedAbsences, maxAbsences)

  return {
    ...subject,
    portalAbsences,
    manualAbsences,
    trackedAbsences,
    configuredHours,
    configuredPeriods,
    maxAbsences,
    manualMaxAbsences,
    remainingAbsences,
    ...riskState,
    maxAbsencesSource:
      manualMaxAbsences != null ? 'manual' : inferredMaxAbsences != null ? 'default' : 'pending',
  }
}

function hydratePeriods(nextPeriods: Period[]) {
  return nextPeriods.map((period) => ({
    ...period,
    subjects: period.subjects.map(deriveSubject),
  }))
}

function subjectStateKey(periodKey: string, subjectName: string) {
  return `${periodKey}::${subjectName}`
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
  const [savingSubjects, setSavingSubjects] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
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

  useEffect(() => {
    if (!notice) {
      return
    }

    const timeoutId = window.setTimeout(() => {
      setNotice((current) => (current === notice ? null : current))
    }, notice.type === 'error' ? 5000 : 3200)

    return () => window.clearTimeout(timeoutId)
  }, [notice])

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
      const searchTarget = (subject.displayName ?? subject.name).toLowerCase()
      const matchesSearch = hasSearch
        ? searchTarget.includes(normalizedSearch)
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
      .map((subject) => subject.trackedAbsences)
      .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
    const pendingLimits = filteredSubjects.filter((subject) => subject.maxAbsencesSource === 'pending').length
    const warningSubjects = filteredSubjects.filter((subject) => {
      return (
        subject.riskLevel === 'attention' ||
        subject.riskLevel === 'limit' ||
        subject.riskLevel === 'exceeded'
      )
    }).length

    return {
      totalAbsences: numericAbsences.reduce((sum, value) => sum + value, 0),
      pendingLimits,
      warningSubjects,
    }
  }, [filteredSubjects])

  function showNotice(type: Notice['type'], text: string) {
    setNotice({ type, text })
  }

  function getDefaultVisibleSubjects(nextPeriods: Period[], periodKey: string) {
    const period = nextPeriods.find((item) => item.key === periodKey) ?? nextPeriods[0] ?? null
    return period?.subjects.map((subject) => subject.name) ?? []
  }

  function updateSubjectState(
    periodKey: string,
    subjectName: string,
    updater: (subject: Subject) => Subject,
  ) {
    setPeriods((current) =>
      current.map((period) =>
        period.key === periodKey
          ? {
              ...period,
              subjects: period.subjects.map((subject) =>
                subject.name === subjectName ? deriveSubject(updater(subject)) : subject,
              ),
            }
          : period,
      ),
    )
  }

  function getSubject(periodKey: string, subjectName: string) {
    return periods
      .find((period) => period.key === periodKey)
      ?.subjects.find((subject) => subject.name === subjectName)
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

        const nextPeriods = hydratePeriods((data.periods ?? []) as Period[])
        if (!nextPeriods.length) {
          throw new Error('Nao foi possivel identificar os periodos na pagina do portal.')
        }

        const nextSelectedPeriodKey = nextPeriods.some((period) => period.key === selectedPeriodKey)
          ? selectedPeriodKey
          : (nextPeriods[0]?.key ?? '')

        setPeriods(nextPeriods)
        setSelectedPeriodKey(nextSelectedPeriodKey)
        setSearchTerm('')
        setVisibleSubjects(getDefaultVisibleSubjects(nextPeriods, nextSelectedPeriodKey))
        showNotice('success', `${nextPeriods.length} periodo(s) carregados.`)

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
      const nextError =
        sessionError instanceof Error
          ? sessionError.message
          : 'Nao foi possivel verificar a sessao atual.'
      setError(nextError)
      showNotice('error', nextError)
    } finally {
      setPreparingDashboard(false)
      setCheckingSession(false)
    }
  }

  async function handleLogin() {
    setPreparingDashboard(true)
    setLoggingIn(true)
    setError(null)

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
      showNotice('info', 'Login realizado com sucesso. Carregando os dados do portal...')
      await syncPortal()
      setAuthenticated(true)
    } catch (loginError) {
      const nextError =
        loginError instanceof Error ? loginError.message : 'Falha ao entrar no portal.'
      setError(nextError)
      showNotice('error', nextError)
    } finally {
      setLoggingIn(false)
      setPreparingDashboard(false)
    }
  }

  async function handleLogout() {
    setError(null)

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
      const nextError =
        syncError instanceof Error ? syncError.message : 'Falha ao carregar periodos.'
      setError(nextError)
      showNotice('error', nextError)
    } finally {
      setSyncing(false)
    }
  }

  async function saveAnnotation(periodKey: string, subjectName: string) {
    const subject = getSubject(periodKey, subjectName)
    if (!subject) {
      return
    }

    const saveKey = subjectStateKey(periodKey, subjectName)
    setSavingSubjects((current) => [...new Set([...current, saveKey])])
    setError(null)

    try {
      const response = await fetch(apiUrl('/api/annotations'), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          periodKey,
          subjectName,
          manualAbsences: subject.manualAbsences,
          maxAbsences: subject.manualMaxAbsences,
          configuredHours: subject.configuredHours,
        }),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.message || 'Nao foi possivel salvar as anotacoes.')
      }

      updateSubjectState(periodKey, subjectName, (current) => ({
        ...current,
        manualAbsences: data.manualAbsences,
        manualMaxAbsences: data.maxAbsences,
        configuredHours: data.configuredHours ?? current.configuredHours,
      }))
      showNotice('success', `Anotacoes salvas em ${subjectName}.`)
    } catch (saveError) {
      const nextError =
        saveError instanceof Error ? saveError.message : 'Nao foi possivel salvar as anotacoes.'
      setError(nextError)
      showNotice('error', nextError)
    } finally {
      setSavingSubjects((current) => current.filter((item) => item !== saveKey))
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
            <p className="hint helper-text">
              No iPhone, abra no Safari e toque em Compartilhar {'>'} Adicionar a Tela de Inicio.
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
      {notice ? (
        <aside className={`toast toast-${notice.type}`} aria-live="polite">
          {notice.text}
        </aside>
      ) : null}

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
              ? `${selectedPeriod.subjects.length} disciplina(s) neste periodo. Agora voce pode acompanhar faltas, carga horaria e limite manualmente.`
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
            <div className="summary-pill">
              <span>Limites pendentes</span>
              <strong>{absenceSummary.pendingLimits}</strong>
            </div>
            <div className="summary-pill">
              <span>Materias em alerta</span>
              <strong>{absenceSummary.warningSubjects}</strong>
            </div>
          </div>
          <p className="hint helper-text">
            Limites conhecidos usam 25% da carga horaria, considerando periodos de 50 minutos.
          </p>
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
                        {subject.displayName ?? subject.name}
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
            {filteredSubjects.map((subject) => {
              const saveKey = subjectStateKey(selectedPeriod.key, subject.name)
              const isSaving = savingSubjects.includes(saveKey)

              return (
                <article
                  className={`subject-card subject-card-${subject.riskLevel ?? 'neutral'}`}
                  key={`${selectedPeriod.key}-${subject.name}`}
                >
                  <div className="subject-card-top">
                    <div>
                      <h3>{subject.displayName ?? subject.name}</h3>
                      <p>{selectedPeriod.label}</p>
                    </div>
                    <span className={`subject-badge badge-${subject.riskLevel ?? 'neutral'}`}>
                      {subject.riskLabel ?? 'Sem status'}
                    </span>
                  </div>

                  <div className="subject-details">
                    <div className="subject-stat-grid">
                      <div className="subject-stat">
                        <span>Faltas acompanhadas</span>
                        <strong>{subject.trackedAbsences ?? 'Sem dado'}</strong>
                      </div>
                      <div className="subject-stat">
                        <span>Limite atual</span>
                        <strong>
                          {subject.maxAbsences != null ? `${subject.maxAbsences} faltas` : 'Pendente'}
                        </strong>
                      </div>
                      <div className="subject-stat">
                        <span>Pode faltar</span>
                        <strong>
                          {subject.remainingAbsences != null
                            ? subject.remainingAbsences
                            : 'Configure o limite'}
                        </strong>
                      </div>
                    </div>

                    <div className="subject-meta">
                      <span>
                        <strong>Status:</strong> {subject.riskMessage ?? 'Sem observacoes'}
                      </span>
                      <span>
                        <strong>Portal:</strong>{' '}
                        {subject.portalAbsences != null ? subject.portalAbsences : 'Sem dado'}
                      </span>
                      <span>
                        <strong>Carga horaria:</strong>{' '}
                        {subject.configuredHours != null
                          ? `${subject.configuredHours} horas (${subject.configuredPeriods} periodos)`
                          : 'Ainda nao configurada'}
                      </span>
                      {subject.subjectCode ? (
                        <span>
                          <strong>Codigo:</strong> {subject.subjectCode}
                        </span>
                      ) : null}
                    </div>

                    <details className="annotation-box">
                      <summary className="annotation-summary">Atualizar faltas e limite</summary>

                      <div className="annotation-grid">
                        <label>
                          Faltas anotadas
                          <input
                            type="number"
                            min="0"
                            value={subject.manualAbsences ?? ''}
                            placeholder={
                              subject.portalAbsences != null
                                ? `Portal: ${subject.portalAbsences}`
                                : 'Ex.: 2'
                            }
                            onChange={(event) => {
                              const value = event.target.value
                              updateSubjectState(selectedPeriod.key, subject.name, (current) => ({
                                ...current,
                                manualAbsences: value === '' ? null : Number(value),
                              }))
                            }}
                          />
                        </label>

                        <label>
                          Carga horaria
                          <input
                            type="number"
                            min="1"
                            value={subject.configuredHours ?? ''}
                            placeholder="Ex.: 60"
                            onChange={(event) => {
                              const value = event.target.value
                              updateSubjectState(selectedPeriod.key, subject.name, (current) => ({
                                ...current,
                                configuredHours: value === '' ? null : Number(value),
                              }))
                            }}
                          />
                        </label>

                        <label>
                          Limite de faltas
                          <input
                            type="number"
                            min="0"
                            value={subject.manualMaxAbsences ?? ''}
                            placeholder={
                              subject.maxAbsencesSource === 'default' && subject.maxAbsences != null
                                ? `Sugerido: ${subject.maxAbsences}`
                                : 'Preencher depois'
                            }
                            onChange={(event) => {
                              const value = event.target.value
                              updateSubjectState(selectedPeriod.key, subject.name, (current) => ({
                                ...current,
                                manualMaxAbsences: value === '' ? null : Number(value),
                              }))
                            }}
                          />
                        </label>
                      </div>

                      <p className="hint helper-text">
                        {subject.maxAbsencesSource === 'default' && subject.maxAbsences != null
                          ? `Sugerimos ${subject.maxAbsences} faltas a partir da carga horaria informada.`
                          : subject.maxAbsencesSource === 'manual'
                          ? 'Esse limite foi ajustado manualmente.'
                          : 'Ainda nao existe um limite configurado para essa materia.'}
                      </p>

                      {subject.history && subject.history.length > 0 ? (
                        <div className="history-box">
                          <strong>Ultimas anotacoes</strong>
                          <div className="history-list">
                            {subject.history.map((entry) => (
                              <div className="history-item" key={entry.id}>
                                <span>{new Date(entry.createdAt).toLocaleString('pt-BR')}</span>
                                <strong>
                                  {entry.manualAbsences != null
                                    ? `${entry.manualAbsences} falta(s)`
                                    : 'Sem faltas anotadas'}
                                </strong>
                                <small>
                                  {entry.maxAbsences != null
                                    ? `Limite ${entry.maxAbsences}`
                                    : 'Limite nao definido'}
                                  {entry.configuredHours != null
                                    ? ` • ${entry.configuredHours}h`
                                    : ''}
                                </small>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}

                      <button
                        className="secondary-button subject-save-button"
                        type="button"
                        onClick={() => void saveAnnotation(selectedPeriod.key, subject.name)}
                        disabled={isSaving}
                      >
                        {isSaving ? 'Salvando...' : 'Salvar anotacoes'}
                      </button>
                    </details>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </section>
    </main>
  )
}
