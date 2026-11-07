import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  ArrowUp, BrainCircuit, Check, ChevronDown, CircleAlert, Database,
  FlaskConical, LoaderCircle, MessageSquareText, MoreHorizontal, Network, PanelLeftClose,
  PanelLeftOpen, Pencil, Plus, RotateCcw, ScanLine, Sparkles, Sun, Moon, Trash2, X,
} from "lucide-react";
import { api, getSessionId, setSessionId } from "./api";
import { useMotionPresence } from "./useMotionPresence";
import { displayText, environmentTableData, fieldLabel, formatTimestamp, isInternalIdField, isTimestampField } from "./display";
import type { AgentResponse, ApiHealth, AppState, Fact } from "./types";

const BeliefGraphPage = lazy(() => import("./BeliefGraphPage"));
type WorkspaceTab = "chat" | "inspector" | "graph";
type Action = "ask" | "scenario-a" | "scenario-b" | "reset" | "new-chat" | "switch-chat" | "delete-chat" | null;
type Chat = { id: string; title: string | null; scenario: "scenario-a" | "scenario-b" | null; customTitle?: boolean; hasMessages?: boolean };
type ChatDialog = { kind: "limit" } | { kind: "delete"; chat: Chat } | { kind: "scenario"; scenario: "scenario-a" | "scenario-b" };
const CHAT_LIST_KEY = "groundtruth-chats-v1";
const MAX_CHATS = 5;
function initialChats(): Chat[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(CHAT_LIST_KEY) ?? "[]");
    if (Array.isArray(parsed)) {
      const chats = parsed.filter((chat): chat is Chat => chat && typeof chat.id === "string" && (chat.title === null || typeof chat.title === "string") && (chat.scenario === null || chat.scenario === "scenario-a" || chat.scenario === "scenario-b")).slice(0, MAX_CHATS);
      if (chats.length) return chats;
    }
  } catch { /* Ignore invalid browser data and retain the current session. */ }
  return [{ id: getSessionId(), title: null, scenario: null }];
}
function chatTitle(question: string): string {
  const clean = question.trim().replace(/\s+/g, " ");
  if (clean.length <= 56) return clean;
  return `${clean.slice(0, 53).replace(/\s+\S*$/, "")}…`;
}
function chatLabel(chat: Chat): string {
  return chat.title || (chat.scenario ? `Scenario ${chat.scenario === "scenario-a" ? "A" : "B"}` : "New conversation");
}
const tabs = [
  { id: "chat", label: "Chat", icon: MessageSquareText },
  { id: "inspector", label: "System inspector", icon: ScanLine },
  { id: "graph", label: "Belief graph", icon: Network },
] as const;
const tabFromPath = (): WorkspaceTab => window.location.pathname === "/graph"
  ? "graph" : window.location.pathname === "/inspector" ? "inspector" : "chat";

const PERSPECTIVE_LABELS: Record<string, string> = {
  user_perspective: "User expectation",
  egocentric_perspective: "Live camera view",
  historical_perspective: "Historical record",
};

const formatTime = formatTimestamp;


function App() {
  const [theme, setTheme] = useState<"dark" | "light">(() => document.documentElement.dataset.theme === "light" ? "light" : "dark");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("groundtruth-theme", theme); } catch { /* Theme still applies without storage. */ }
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#191919" : "#ffffff");
  }, [theme]);
  const [chats, setChats] = useState<Chat[]>(initialChats);
  const [activeChatId, setActiveChatId] = useState(() => {
    const current = getSessionId();
    const saved = initialChats();
    const id = saved.some((chat) => chat.id === current) ? current : saved[0].id;
    setSessionId(id);
    return id;
  });
  const [state, setState] = useState<AppState | null>(null);
  const [health, setHealth] = useState<ApiHealth | null>(null);
  const [question, setQuestion] = useState("");
  const [action, setAction] = useState<Action>(null);
  const [error, setError] = useState<string | null>(null);
  const [openChatMenu, setOpenChatMenu] = useState<string | null>(null);
  const [renamingChat, setRenamingChat] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [chatDialog, setChatDialog] = useState<ChatDialog | null>(null);
  const chatMenuTrigger = useRef<HTMLButtonElement | null>(null);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>(tabFromPath);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 760);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = Number(window.localStorage.getItem("groundtruth-sidebar-width"));
    return Number.isFinite(saved) && saved >= 200 && saved <= 400 ? saved : 232;
  });
  const sidebarDrag = useRef<{ x: number; width: number } | null>(null);
  const resizeSidebar = (width: number) => {
    const next = Math.max(200, Math.min(400, window.innerWidth - 500, width));
    setSidebarWidth(next);
    window.localStorage.setItem("groundtruth-sidebar-width", String(next));
  };
  const [loadedScenario, setLoadedScenario] = useState<"scenario-a" | "scenario-b" | null>(null);
  const [graphSidebarTarget, setGraphSidebarTarget] = useState<HTMLDivElement | null>(null);
  const conversationEnd = useRef<HTMLDivElement>(null);
  const questionInput = useRef<HTMLTextAreaElement>(null);
  const sidebarToggle = useRef<HTMLButtonElement>(null);

  const refresh = useCallback(async () => {
    try {
      const apiHealth = await api.health();
      setHealth(apiHealth);
      const currentState = await api.state();
      setState(currentState);
      const firstQuestion = currentState.history[0]?.question;
      setChats((items) => items.map((chat) => chat.id === getSessionId() ? { ...chat, hasMessages: !!firstQuestion, title: firstQuestion && !chat.title ? chatTitle(firstQuestion) : chat.title } : chat));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not connect to the Python Agent API.");
    }
  }, []);

  useEffect(() => { localStorage.setItem(CHAT_LIST_KEY, JSON.stringify(chats)); }, [chats]);
  useEffect(() => {
    if (!openChatMenu) return;
    const closeOutside = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest("[data-chat-menu]")) setOpenChatMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setOpenChatMenu(null);
        chatMenuTrigger.current?.focus();
      }
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [openChatMenu]);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const onPopState = () => setActiveTab(tabFromPath());
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && window.innerWidth <= 760) {
        setSidebarOpen(false);
        sidebarToggle.current?.focus();
      }
    };
    const compact = window.matchMedia("(max-width: 760px)");
    const onBreakpoint = () => setSidebarOpen(!compact.matches);
    window.addEventListener("popstate", onPopState);
    window.addEventListener("keydown", onEscape);
    compact.addEventListener("change", onBreakpoint);
    return () => {
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener("keydown", onEscape);
      compact.removeEventListener("change", onBreakpoint);
    };
  }, []);
  useEffect(() => {
    if (activeTab === "chat") conversationEnd.current?.scrollIntoView({ block: "end" });
  }, [state?.history.length, activeTab, action]);

  const selectTab = (tab: WorkspaceTab) => {
    setActiveTab(tab);
    window.history.pushState({}, "", tab === "chat" ? "/" : `/${tab}`);
    if (window.innerWidth <= 760) setSidebarOpen(false);
  };
  const runQuestion = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!question.trim() || health?.status !== "ok" || action) return;
    setAction("ask");
    setError(null);
    try {
      const submitted = question.trim();
      setState(await api.ask(submitted));
      setChats((items) => items.map((chat) => chat.id === activeChatId ? { ...chat, hasMessages: true, title: chat.title || chatTitle(submitted) } : chat));
      setQuestion("");
      requestAnimationFrame(() => questionInput.current?.focus());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The Agent request failed.");
    } finally { setAction(null); }
  };
  const loadScenario = async (scenario: "scenario-a" | "scenario-b", confirmed = false) => {
    if (!health || action) return;
    if (state?.history.length && !confirmed) {
      setChatDialog({ kind: "scenario", scenario });
      return;
    }
    setChatDialog(null);
    setAction(scenario);
    setError(null);
    try {
      const updatedState = await api.loadScenario(scenario);
      setState(updatedState);
      setQuestion(updatedState.suggested_question);
      setLoadedScenario(scenario);
      setChats((items) => items.map((chat) => chat.id === activeChatId ? { ...chat, hasMessages: false, title: chat.customTitle ? chat.title : null, scenario } : chat));
      if (window.innerWidth <= 760) setSidebarOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The scenario could not be loaded.");
    } finally { setAction(null); }
  };
  const switchChat = async (id: string) => {
    if (action || id === activeChatId) return;
    const previous = activeChatId;
    setAction("switch-chat");
    setError(null);
    setSessionId(id);
    try {
      const next = await api.state();
      setChats((items) => items.map((chat) => chat.id === id ? { ...chat, hasMessages: next.history.length > 0 } : chat));
      setActiveChatId(id);
      setState(next);
      setQuestion("");
      setLoadedScenario(chats.find((chat) => chat.id === id)?.scenario ?? null);
      selectTab("chat");
    } catch (cause) {
      setSessionId(previous);
      setError(cause instanceof Error ? cause.message : "Could not open this chat.");
    } finally { setAction(null); }
  };
  const newChat = async () => {
    if (!health || action) return;
    if (chats.some((chat) => chat.hasMessages === false)) return;
    setAction("new-chat");
    try {
      const unchecked = chats.filter((chat) => chat.hasMessages === undefined);
      const checked = await Promise.all(unchecked.map(async (chat) => ({ id: chat.id, hasMessages: (await api.state(chat.id)).history.length > 0 })));
      if (checked.length) setChats((items) => items.map((chat) => ({ ...chat, hasMessages: checked.find((item) => item.id === chat.id)?.hasMessages ?? chat.hasMessages })));
      if (chats.some((chat) => (checked.find((item) => item.id === chat.id)?.hasMessages ?? chat.hasMessages) === false)) {
        return;
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not check your chats.");
      return;
    } finally { setAction(null); }
    if (chats.length >= MAX_CHATS) {
      setChatDialog({ kind: "limit" });
      return;
    }
    const previous = activeChatId;
    const id = crypto.randomUUID();
    setAction("new-chat");
    setError(null);
    setSessionId(id);
    try {
      const next = await api.state();
      setChats((items) => [...items, { id, title: null, scenario: null, hasMessages: false }]);
      setActiveChatId(id);
      setState(next);
      setQuestion("");
      setLoadedScenario(null);
      selectTab("chat");
      requestAnimationFrame(() => questionInput.current?.focus());
    } catch (cause) {
      setSessionId(previous);
      setError(cause instanceof Error ? cause.message : "Could not start a new chat.");
    } finally { setAction(null); }
  };
  const saveChatName = (id: string) => {
    const title = renameValue.trim().replace(/\s+/g, " ");
    if (!title) return;
    setChats((items) => items.map((chat) => chat.id === id ? { ...chat, title, customTitle: true } : chat));
    setRenamingChat(null);
  };
  const deleteChat = async (chat: Chat) => {
    if (!health || action) return;
    setChatDialog(null);
    const previous = activeChatId;
    const remaining = chats.filter((item) => item.id !== chat.id);
    setAction("delete-chat");
    setError(null);
    setSessionId(chat.id);
    try {
      await api.deleteSession();
      if (chat.id === previous) {
        const nextChat = remaining[0] ?? { id: crypto.randomUUID(), title: null, scenario: null, hasMessages: false };
        if (!remaining.length) remaining.push(nextChat);
        setSessionId(nextChat.id);
        setActiveChatId(nextChat.id);
        setState(null);
        setQuestion("");
        setLoadedScenario(nextChat.scenario);
        setChats(remaining);
        try { setState(await api.state()); }
        catch (cause) { setError(cause instanceof Error ? cause.message : "Could not open the remaining chat."); }
        selectTab("chat");
      } else {
        setSessionId(previous);
        setChats(remaining);
      }
    } catch (cause) {
      setSessionId(previous);
      setError(cause instanceof Error ? cause.message : "Could not delete this chat.");
    } finally { setAction(null); }
  };
  const resetAgent = async () => {
    if (!health || action) return;
    if (!window.confirm("Reset this chat's Agent and clear its messages? Other chats will not be affected.")) return;
    setAction("reset");
    setError(null);
    try {
      setState(await api.reset());
      setQuestion("");
      setLoadedScenario(null);
      setChats((items) => items.map((chat) => chat.id === activeChatId ? { ...chat, hasMessages: false, title: chat.customTitle ? chat.title : null, scenario: null } : chat));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Agent state could not be reset.");
    } finally { setAction(null); }
  };
  const busy = action !== null;
  const backendReachable = health?.status === "ok" || health?.status === "degraded";
  const providerAvailable = health?.status === "ok";
  const activeChat = chats.find((chat) => chat.id === activeChatId);

  return (
    <div className={`workspace ${sidebarOpen ? "sidebar-visible" : "sidebar-hidden"}`} style={{ "--sidebar-width": `${sidebarWidth}px` } as React.CSSProperties}>
      {sidebarOpen && <button className="workspace-backdrop" type="button" aria-label="Close sidebar" onClick={() => setSidebarOpen(false)} />}
      <aside className="workspace-sidebar" aria-label="Agent workspace" style={!sidebarOpen ? { visibility: "hidden" } : undefined}>
        <div className="workspace-brand">
          <BrainCircuit size={25} strokeWidth={1.6} aria-hidden="true" />
          <span>GroundTruth</span>
          <button type="button" className="icon-button" aria-label="Hide sidebar" onClick={() => { setSidebarOpen(false); requestAnimationFrame(() => sidebarToggle.current?.focus()); }}><PanelLeftClose size={18} /></button>
        </div>
        <div className="workspace-new-chat">
          <button type="button" className="new-chat-button" onClick={() => void newChat()} disabled={!backendReachable || busy}>
            {action === "new-chat" ? <LoaderCircle size={18} className="spin" /> : <Plus size={18} />}<span>New chat</span>
          </button>
        </div>
        <nav className="workspace-nav" aria-label="Workspace tabs">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" className={`workspace-nav-item ${activeTab === id ? "active" : ""}`} aria-current={activeTab === id ? "page" : undefined} onClick={() => selectTab(id)}>
              <Icon size={19} strokeWidth={1.6} /><span>{label}</span>
            </button>
          ))}
        </nav>
        {activeTab === "chat" && <div className="workspace-scenarios" role="group" aria-label="Scenario presets">
          {(["scenario-a", "scenario-b"] as const).map((scenario, index) => (
            <button key={scenario} type="button" className={`workspace-nav-item ${loadedScenario === scenario ? "scenario-loaded" : ""}`} onClick={() => void loadScenario(scenario)} disabled={!backendReachable || busy} aria-pressed={loadedScenario === scenario}>
              {action === scenario ? <LoaderCircle size={18} className="spin" /> : <FlaskConical size={18} strokeWidth={1.6} />}
              <span>Load Scenario {index === 0 ? "A" : "B"}</span>
              {loadedScenario === scenario && <Check size={15} className="scenario-check" />}
            </button>
          ))}
        </div>}
        {activeTab === "chat" && <section className="chat-list" aria-label="Saved chats">
          <div className="chat-list-heading"><h2>Chats</h2></div>
          {chats.map((chat) => <div className={`chat-list-item ${chat.id === activeChatId ? "active" : ""}`} key={chat.id}>
            {renamingChat === chat.id ? <form className="chat-rename" onSubmit={(event) => { event.preventDefault(); saveChatName(chat.id); }} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setRenamingChat(null); } }}>
              <input autoFocus aria-label="Chat name" maxLength={80} value={renameValue} onChange={(event) => setRenameValue(event.target.value)} />
              <button type="submit" aria-label="Save chat name" disabled={!renameValue.trim()}><Check size={15} /></button>
              <button type="button" aria-label="Cancel rename" onClick={() => setRenamingChat(null)}><X size={15} /></button>
            </form> : <>
              <button type="button" className="chat-list-select" title={displayText(chatLabel(chat))} aria-current={chat.id === activeChatId ? "page" : undefined} disabled={busy} onClick={() => void switchChat(chat.id)}><MessageSquareText size={16} /><span>{displayText(chatLabel(chat))}</span></button>
              <div className="chat-menu-wrap" data-chat-menu>
                <button type="button" className="chat-menu-trigger" aria-label={`Options for ${chatLabel(chat)}`} aria-expanded={openChatMenu === chat.id} aria-haspopup="menu" disabled={busy} onClick={(event) => { chatMenuTrigger.current = event.currentTarget; setOpenChatMenu((current) => current === chat.id ? null : chat.id); }}><MoreHorizontal size={18} /></button>
                <ChatMenu chat={chat} open={openChatMenu === chat.id} onRename={() => { setRenameValue(chatLabel(chat)); setRenamingChat(chat.id); setOpenChatMenu(null); }} onDelete={() => { setOpenChatMenu(null); setChatDialog({ kind: "delete", chat }); }} />
              </div>
            </>}
          </div>)}
        </section>}
        {activeTab === "graph" && <div ref={setGraphSidebarTarget} className="workspace-graph-controls" />}
        <div className="workspace-sidebar-footer">
          <button type="button" className="workspace-nav-item" onClick={() => void resetAgent()} disabled={!backendReachable || busy}>
            {action === "reset" ? <LoaderCircle size={18} className="spin" /> : <RotateCcw size={18} strokeWidth={1.6} />}<span>Reset current Agent</span>
          </button>

        </div>
      </aside>
      {sidebarOpen && <div className="workspace-sidebar-resizer" role="separator" tabIndex={0} aria-label="Resize sidebar" aria-orientation="vertical" aria-valuemin={200} aria-valuemax={Math.max(200, Math.min(400, window.innerWidth - 500))} aria-valuenow={sidebarWidth}
        onPointerDown={(event) => { sidebarDrag.current = { x: event.clientX, width: sidebarWidth }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={(event) => { if (sidebarDrag.current) resizeSidebar(sidebarDrag.current.width + event.clientX - sidebarDrag.current.x); }}
        onPointerUp={() => { sidebarDrag.current = null; }} onPointerCancel={() => { sidebarDrag.current = null; }}
        onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); resizeSidebar(sidebarWidth + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 40 : 16)); } }} />}
      <main className={`workspace-main view-${activeTab}`}>
        <header className="workspace-header">
          <div className="workspace-heading">
            <button ref={sidebarToggle} type="button" className="icon-button" aria-label={sidebarOpen ? "Hide sidebar" : "Show sidebar"} aria-expanded={sidebarOpen} onClick={() => setSidebarOpen((open) => !open)}><PanelLeftOpen size={19} /></button>
            <h1 title={activeTab === "chat" && activeChat ? displayText(chatLabel(activeChat)) : undefined}>{activeTab === "chat" && activeChat ? displayText(chatLabel(activeChat)) : tabs.find((tab) => tab.id === activeTab)?.label}</h1>
          </div>

          <button type="button" className="icon-button theme-toggle" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}>{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button>
        </header>
        {health?.status === "degraded" && health.error && <div className="error-banner" role="status"><CircleAlert size={17} /><span>{health.error}</span></div>}
        {error && <div className="error-banner" role="alert"><CircleAlert size={17} /><span>{error}</span><button type="button" onClick={() => void refresh()}>Retry connection</button><button type="button" aria-label="Dismiss error" onClick={() => setError(null)}><X size={16} /></button></div>}
        {activeTab === "chat" && <section className="chat-workspace" aria-label="Chat">
          <div className="conversation" role="log" aria-label="Conversation" aria-busy={action === "ask"}>
            <div className={`conversation-inner ${!state?.history.length ? "is-empty" : ""}`}>
              {state?.history.length ? state.history.map((response, index) => <ConversationTurn key={`${displayText(response.question)}-${index}`} response={response} />) : (
                <div className="empty-conversation">
                  <div className="welcome-mark"><BrainCircuit size={34} strokeWidth={1.4} /></div>
                  <h2>What would you like to know?</h2>
                  <p>Ask about the current world.<br />Explore the evidence behind every answer.</p>
                </div>
              )}
              {action === "ask" && <div className="working-indicator" role="status"><LoaderCircle size={16} className="spin" />Running the Agent cycle…</div>}
              <div ref={conversationEnd} />
            </div>
          </div>
          <form className="composer-wrap" onSubmit={(event) => void runQuestion(event)}>
            <label className="sr-only" htmlFor="agent-question">Ask the cognitive Agent</label>
            <div className="composer">
              <textarea ref={questionInput} id="agent-question" rows={2} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); }
              }} placeholder="Ask anything about the current world…" disabled={!providerAvailable || busy} />
              <div className="composer-footer">
                <button className="send-button" type="submit" aria-label="Send message" disabled={!providerAvailable || busy || !question.trim()}>{action === "ask" ? <LoaderCircle size={18} className="spin" /> : <ArrowUp size={18} />}</button>
              </div>
            </div>
            <p className="composer-hint">Enter to send <span>·</span> Shift + Enter for a new line</p>
          </form>
        </section>}
        {activeTab === "inspector" && <Inspector state={state} />}
        {activeTab === "graph" && <Suspense fallback={<div className="route-loading">Loading belief graph…</div>}><BeliefGraphPage embedded workspaceState={state} workspaceHealth={health} sidebarTarget={graphSidebarTarget} /></Suspense>}
      </main>
      {chatDialog && <ChatActionDialog dialog={chatDialog} onClose={() => setChatDialog(null)} onDelete={(chat) => void deleteChat(chat)} onLoadScenario={(scenario) => void loadScenario(scenario, true)} />}
    </div>
  );
}

function ChatMenu({ chat, open, onRename, onDelete }: { chat: Chat; open: boolean; onRename: () => void; onDelete: () => void }) {
  const { present, phase } = useMotionPresence(open, "--dropdown-close-dur");
  const menuRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (present && open) menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [present, open]);
  if (!present) return null;
  return <div ref={(element) => { menuRef.current = element; if (element) element.inert = !open; }} className={`chat-menu t-dropdown ${phase === "open" ? "is-open" : phase === "exit" ? "is-closing" : ""}`} data-origin="top-right" role="menu" aria-hidden={!open} aria-label={`Options for ${chatLabel(chat)}`} onKeyDown={(event) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button"));
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  }}>
    <button type="button" role="menuitem" tabIndex={-1} onClick={onRename}><Pencil size={15} />Rename</button>
    <button type="button" role="menuitem" tabIndex={-1} className="chat-menu-delete" onClick={onDelete}><Trash2 size={15} />Delete</button>
  </div>;
}

function ChatActionDialog({ dialog, onClose, onDelete, onLoadScenario }: { dialog: ChatDialog; onClose: () => void; onDelete: (chat: Chat) => void; onLoadScenario: (scenario: "scenario-a" | "scenario-b") => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useEffect(() => { dialogRef.current?.showModal(); }, []);
  const deleting = dialog.kind === "delete";
  return <dialog ref={dialogRef} className="chat-dialog" aria-labelledby={titleId} aria-describedby={descriptionId} onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <h2 id={titleId}>{deleting ? "Delete chat?" : dialog.kind === "scenario" ? `Load Scenario ${dialog.scenario === "scenario-a" ? "A" : "B"}?` : "Chat limit reached"}</h2>
    <p id={descriptionId}>{deleting ? <>Delete <strong>{chatLabel(dialog.chat)}</strong> and its Agent state? This cannot be undone.</> : dialog.kind === "scenario" ? "Loading this scenario will replace the messages and Agent state in this chat. Other chats will not be affected." : "You can have up to five chats. Delete one to create another."}</p>
    <div className="chat-dialog-actions">
      {deleting ? <><button type="button" className="chat-dialog-secondary" autoFocus onClick={onClose}>Cancel</button><button type="button" className="chat-dialog-danger" onClick={() => onDelete(dialog.chat)}>Delete chat</button></> : dialog.kind === "scenario" ? <><button type="button" className="chat-dialog-secondary" autoFocus onClick={onClose}>Cancel</button><button type="button" className="chat-dialog-primary" onClick={() => onLoadScenario(dialog.scenario)}>Load scenario</button></> : <button type="button" className="chat-dialog-primary" autoFocus onClick={onClose}>Got it</button>}
    </div>
  </dialog>;
}

function ConversationTurn({ response }: { response: AgentResponse }) {
  const [showTrace, setShowTrace] = useState(false);
  const trace = response.react_trace;

  return (
    <article className="conversation-turn">
      <div className="user-message">{displayText(response.question)}</div>
      <div className="assistant-message">
        <div className="agent-heading">
          <div className="agent-icon"><BrainCircuit size={16} /></div>
          <div><strong>Agent</strong><span>Grounded reasoning assistant</span></div>
          <div className="status-badges-group">
            {trace && trace.explanation_source !== "llm" && (
              <span className={`trace-source-badge source-${trace.explanation_source}`}>
                {trace.explanation_source === "rule_based" ? "Rule-based" : "Deterministic Fallback"}
              </span>
            )}
            <span className={`status-badge status-${response.status}`}>{response.status.replaceAll("_", " ")}</span>
          </div>
        </div>

        <div className="answer-text"><ReactMarkdown skipHtml remarkPlugins={[formatMarkdownIdentifiers]}>{response.answer}</ReactMarkdown></div>
        {response.perspectives && (
          <div className="perspective-cards">
            {Object.entries(response.perspectives).map(([key, value]) => (
              <div className="perspective-card" key={key}>
                <span>{PERSPECTIVE_LABELS[key] ?? key.replaceAll("_", " ")}</span>
                <p>{displayText(value)}</p>
              </div>
            ))}
          </div>
        )}
        {response.audit_trails.length > 0 && <AuditTrailInline response={response} />}
        {response.revisions.length > 0 && (
          <div className="revision-inline">
            <div className="revision-heading"><Check size={15} /><strong>Belief revision recorded</strong></div>
            {response.revisions.map((revision) => (
              <p key={revision.audit_event.event_id}>
                {displayText(revision.audit_event.reason)} · rule <code>{displayText(revision.audit_event.policy_rule)}</code>
              </p>
            ))}
          </div>
        )}
        {trace && (
          <div className="react-trace-container">
            <button
              type="button"
              className="react-trace-toggle"
              onClick={() => setShowTrace((open) => !open)}
              aria-expanded={showTrace}
            >
              <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                <Sparkles size={12} />
                <span>ReAct Tool Cycle: <code>{displayText(trace.tool_name)}</code></span>
              </span>
              <ChevronDown size={13} className={`trace-chevron ${showTrace ? "open" : ""}`} />
            </button>
            {showTrace && (
              <div className="react-trace-details">
                <div className="react-step-row">
                  <span className="step-label"><span>1.</span><span>Proposed Tool</span></span>
                  <div className="step-content">
                    <><strong>{displayText(trace.tool_name)}</strong><StructuredValue value={trace.arguments} /></>
                  </div>
                </div>
                <div className="react-step-row">
                  <span className="step-label"><span>2.</span><span>Approved Operations</span></span>
                  <div className="step-content">
                    {trace.approved_operations.length > 0 ? (
                      <div className="ops-list">
                        {trace.approved_operations.map((op, i) => (
                          <span key={i} className="op-tag"><Check size={11} /> {displayText(op)}</span>
                        ))}
                      </div>
                    ) : (
                      <span className="empty-detail">No external operations required</span>
                    )}
                  </div>
                </div>
                <div className="react-step-row">
                  <span className="step-label"><span>3.</span><span>Executed Values &amp; Evidence</span></span>
                  <div className="step-content">
                    {trace.operations && trace.operations.length > 0 ? (
                      <div className="trace-ops-detail">
                        {trace.operations.map((op, i) => (
                          <div key={i} className="trace-op-item">
                            <div className="trace-op-header">
                              <span className={`op-status-badge status-${op.status}`}>
                                {op.ran ? <Check size={10} /> : null} {displayText(op.name)} ({displayText(op.status)})
                              </span>
                            </div>
                            <div className="trace-op-summary">{displayText(op.summary)}</div>
                          </div>
                        ))}
                      </div>
                    ) : response.revisions.length > 0 ? (
                      <span className="highlight-tag">{response.revisions.length} belief revision recorded</span>
                    ) : response.sensor_telemetry.length > 0 ? (
                      <span className="highlight-tag">{response.sensor_telemetry.length} live sensor reading(s)</span>
                    ) : (
                      <span className="highlight-tag">Active memory facts verified</span>
                    )}
                  </div>
                </div>
                <div className="react-step-row">
                  <span className="step-label"><span>4.</span><span>Response Synthesis</span></span>
                  <div className="step-content">
                    <span className="model-tag">Source: {displayText(trace.explanation_source)}</span>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

      </div>
    </article>
  );
}

function AuditTrailInline({ response }: { response: AgentResponse }) {
  const facts = [
    ...new Map(
      response.audit_trails
        .flatMap((trail) => trail.facts)
        .map((fact) => [fact.fact_id, fact]),
    ).values(),
  ].sort((left, right) => left.created_at.localeCompare(right.created_at));
  const events = [
    ...new Map(
      response.audit_trails
        .flatMap((trail) => trail.events)
        .map((event) => [event.event_id, event]),
    ).values(),
  ];

  return (
    <section className="audit-trail-inline" aria-label="Retrieved belief history">
      <div className="audit-trail-heading">
        <Database size={15} />
        <strong>Belief history retrieved</strong>
        <span>{events.length} revision {events.length === 1 ? "event" : "events"}</span>
      </div>
      <div className="audit-fact-chain">
        {facts.map((fact, index) => (
          <div className={`audit-fact ${fact.superseded_by ? "superseded" : "active"}`} key={fact.fact_id}>
            <span>{fact.superseded_by ? "Previous belief" : "Current belief"}</span>
            <strong>{displayText(fact.subject)} <code>{displayText(fact.predicate)}</code> {displayText(fact.object)}</strong>
            <small>{displayText(fact.source_agent)} · {Math.round(fact.confidence_score * 100)}% confidence</small>
            {index < facts.length - 1 && <i aria-hidden="true">→</i>}
          </div>
        ))}
      </div>
      {events.map((event) => (
        <div className="audit-event-explanation" key={event.event_id}>
          <p>{displayText(event.reason)}</p>
          <small>Policy: <code>{displayText(event.policy_rule)}</code> · {formatTime(event.created_at)}</small>
        </div>
      ))}
    </section>
  );
}

function InspectorSection({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return <div className="inspector-section t-acc" data-open={open}>
    <button type="button" className="inspector-section-heading t-acc-head" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)}>
      <span>{title}</span><span className="t-acc-chevron"><ChevronDown size={16} /></span>
    </button>
    <div className="t-acc-panel" id={panelId} aria-hidden={!open} ref={(element) => { if (element) element.inert = !open; }}>
      <div className="t-acc-panel-inner">{children}</div>
    </div>
  </div>;
}

function Inspector({ state }: { state: AppState | null }) {
  const latest = state?.history.at(-1) ?? null;
  return (
    <section className="inspector" aria-label="System inspector">
      <header className="inspector-header">
        <div><h2>Inside the Agent</h2><p>Reasoning, evidence, and the current world state.</p></div>
      </header>
      <div className="inspector-content">
        <InspectorSection title="Tier 1 · graph &amp; evidence ledger">
          <div className="inspector-section-body">
            {state ? (
              <>
                <div className="count-grid">
                  <ValueCard label="Active nodes">{state.graph.node_count}</ValueCard>
                  <ValueCard label="Active edges">{state.graph.edge_count}</ValueCard>
                </div>
                <ValueCard label="Active graph nodes">{state.graph.nodes.length ? state.graph.nodes.map(displayText).join(", ") : "No active graph nodes."}</ValueCard>
                <InspectorCard label="Active belief graph" className="table-card">
                  {state.graph.edges.length ? (
                    <div className="table-scroll"><table><thead><tr><th>Subject</th><th>Predicate</th><th>Object</th><th>Source</th></tr></thead><tbody>
                      {state.graph.edges.map((edge) => <tr key={edge.key}><td>{displayText(edge.source)}</td><td>{displayText(edge.predicate)}</td><td>{displayText(edge.target)}</td><td>{displayText(edge.source_agent)}</td></tr>)}
                    </tbody></table></div>
                  ) : <p className="empty-detail">No active edges in the graph.</p>}
                </InspectorCard>
                <LedgerTable facts={state.ledger} />
              </>
            ) : <p className="empty-detail">Agent state has not been retrieved.</p>}
          </div>
        </InspectorSection>

        <InspectorSection title="Tier 2 · reasoning &amp; outcome">
          <div className="inspector-section-body">
            <ValueCard label="Response status">{latest?.status ? displayText(latest.status) : "No Agent response yet."}</ValueCard>
            <ValueCard label="Plan rationale">{latest?.plan_reason ? displayText(latest.plan_reason) : "No plan has run yet."}</ValueCard>
            <ValueCard label="Plan verification">{latest?.plan_verifiable === true ? "Verifiable" : latest?.plan_verifiable === false ? "Unverifiable" : "No executable plan was created."}</ValueCard>
            {latest?.react_trace && (
              <InspectorCard label="ReAct Execution Cycle">
                <div className="trace-kv">
                  <div><strong>Proposed Tool:</strong> <code>{displayText(latest.react_trace.tool_name)}</code></div>
                  <div><strong>Arguments:</strong> <StructuredValue value={latest.react_trace.arguments} /></div>
                  <div><strong>Approved Operations:</strong> {latest.react_trace.approved_operations.map(displayText).join(", ") || "None"}</div>
                  <div><strong>Source:</strong> {displayText(latest.react_trace.explanation_source)}</div>
                  {latest.react_trace.operations && latest.react_trace.operations.length > 0 && (
                    <div style={{ marginTop: "8px" }}>
                      <strong style={{ fontSize: "11px", display: "block", marginBottom: "5px" }}>
                        Executed Operations &amp; Evidence:
                      </strong>
                      <div className="trace-ops-detail">
                        {latest.react_trace.operations.map((op, idx) => (
                          <div key={idx} className="trace-op-item">
                            <div className="trace-op-header">
                              <span className={`op-status-badge status-${op.status}`}>
                                {op.ran ? <Check size={10} /> : null} {displayText(op.name)}
                              </span>
                            </div>
                            <div className="trace-op-summary">{displayText(op.summary)}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </InspectorCard>
            )}
            <InspectorCard label="Belief revisions and audit events">
              {state?.audit_events.length ? state.audit_events.map((event) => <AuditEventView key={event.event_id} event={event} />) : <p className="empty-detail">No audit events have been recorded.</p>}
            </InspectorCard>
            <ValueCard label="Sensor telemetry">
              {latest?.sensor_telemetry.length ? <StructuredValue value={latest.sensor_telemetry} /> : "No observation has been returned yet."}
            </ValueCard>
            {latest?.audit_trails.map((trail) => (
              <InspectorCard key={trail.root_fact_id} label="Audit chain">
                <p className="empty-detail">{trail.facts.length} related fact(s), {trail.events.length} revision event(s).</p>
              </InspectorCard>
            ))}
          </div>
        </InspectorSection>

        <InspectorSection title="Tier 3 · physical environment">
          <div className="inspector-section-body">
            {state ? (
              <>
                {state.environment.scenario_name && <ValueCard label="Loaded scenario">{displayText(state.environment.scenario_name)}</ValueCard>}
                <ValueCard label="World version">{state.environment.world_version}</ValueCard>
                <ValueCard label="Robot pose"><StructuredValue value={state.environment.robot} /></ValueCard>
                <ValueCard label="Ambient lighting"><StructuredValue value={state.environment.light} /></ValueCard>
                <InspectorCard label={`Obstacles (${Object.keys(state.environment.obstacles).length})`} className="table-card"><EnvironmentTable records={state.environment.obstacles} idField="obstacle_id" /></InspectorCard>
                <InspectorCard label={`Objects (${Object.keys(state.environment.objects).length})`} className="table-card"><EnvironmentTable records={state.environment.objects} idField="object_id" /></InspectorCard>
                <ValueCard label="Advertised sensor capabilities">{state.capabilities.filter((item) => item.kind === "sensor").map((item) => displayText(item.name)).join(", ") || "No sensor capabilities are advertised."}</ValueCard>
              </>
            ) : <p className="empty-detail">Environment state has not been retrieved.</p>}
          </div>
        </InspectorSection>
        <InspectorSection title="Perspectives">
          <div className="inspector-section-body">
            {latest?.perspectives
              ? Object.entries(latest.perspectives).map(([key, value]) => <ValueCard key={key} label={PERSPECTIVE_LABELS[key] ?? displayText(key)}>{value}</ValueCard>)
              : <p className="empty-detail">No perspective result has been returned yet.</p>}
          </div>
        </InspectorSection>
      </div>
    </section>
  );
}

function InspectorCard({ label, children, className = "" }: { label: string; children: React.ReactNode; className?: string }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const contentId = useId();
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);

  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const measure = () => {
      if (content.getBoundingClientRect().width > 0) setOverflows(content.scrollHeight > 180);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    measure();
    return () => observer.disconnect();
  }, [children]);

  return (
    <div className={`inspector-card ${className}`}>
      <p className="value-label">{label}</p>
      <div id={contentId} className={`inspector-card-preview ${overflows && !expanded ? "collapsed" : ""}`}>
        <div ref={contentRef}>{children}</div>
      </div>
      {overflows && <button type="button" className="inspector-card-toggle" aria-expanded={expanded} aria-controls={contentId} onClick={() => setExpanded((value) => !value)}>
        {expanded ? "Show less" : "Show more"}<ChevronDown size={15} className={expanded ? "expanded" : ""} />
      </button>}
    </div>
  );
}

function ValueCard({ label, children }: { label: string; children: React.ReactNode }) {
  return <InspectorCard label={label}><div className="value-content">{children}</div></InspectorCard>;
}

function formatMarkdownIdentifiers() {
  const visit = (node: unknown) => {
    if (typeof node !== "object" || node === null) return;
    const item = node as { type?: string; value?: string; children?: unknown[] };
    if ((item.type === "text" || item.type === "inlineCode" || item.type === "code") && typeof item.value === "string") item.value = displayText(item.value);
    item.children?.forEach(visit);
  };
  return (tree: unknown) => visit(tree);
}

function EnvironmentTable({ records, idField }: { records: Record<string, Record<string, unknown>>; idField: string }) {
  const { rows, columns } = environmentTableData(records, idField);
  if (!rows.length) return <p className="empty-detail">None recorded.</p>;
  return <div className="table-scroll environment-table-scroll" role="region" aria-label="Recorded readings, scroll horizontally for more fields" tabIndex={0}><table className="environment-table">
    <thead><tr><th scope="col">Item</th>{columns.map((key) => <th scope="col" key={key}>{fieldLabel(key)}</th>)}</tr></thead>
    <tbody>{rows.map(({ id, fields }) => <tr key={id}><th scope="row">{displayText(id)}</th>{columns.map((key) => <td key={key}>{key in fields ? <StructuredValue value={fields[key]} field={key} /> : "—"}</td>)}</tr>)}</tbody>
  </table></div>;
}

function StructuredValue({ value, field = "" }: { value: unknown; field?: string }): React.ReactNode {
  if (value === null || value === undefined) return <span>Not recorded</span>;
  if (typeof value === "boolean") return <span>{value ? "Yes" : "No"}</span>;
  if (typeof value === "string") return <span>{value ? (isTimestampField(field) ? formatTimestamp(value) : displayText(value)) : "Empty"}</span>;
  if (typeof value === "number") return <span>{String(value)}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span>None recorded</span>;
    return <ol className="inspector-value-list">{value.map((item, index) => (
      <li key={index}><StructuredValue value={item} /></li>
    ))}</ol>;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value).filter(([key]) => !isInternalIdField(key));
    if (!entries.length) return <span>None recorded</span>;
    return <dl className="inspector-fields">{entries.map(([key, item]) => (
      <div className="inspector-field" key={key}>
        <dt>{fieldLabel(key)}</dt><dd><StructuredValue value={item} field={key} /></dd>
      </div>
    ))}</dl>;
  }
  return <span>{String(value)}</span>;
}

function AuditEventView({ event }: { event: AgentResponse["audit_events"][number] }) {
  return (
    <div className="audit-event">
      <div className="audit-rule"><Database size={13} /><code>{displayText(event.policy_rule)}</code></div>
      <p>{displayText(event.reason)}</p>
      <small>{formatTime(event.created_at)}</small>
    </div>
  );
}

function LedgerTable({ facts }: { facts: Fact[] }) {
  return (
    <InspectorCard label={`SQLite evidence ledger · ${facts.length} fact(s)`} className="table-card">
      {facts.length ? (
        <div className="table-scroll"><table><thead><tr><th>Claim</th><th>Source</th><th>State</th></tr></thead><tbody>
          {facts.map((fact) => (
            <tr key={fact.fact_id}>
              <td>{displayText(fact.subject)} {displayText(fact.predicate)} {displayText(fact.object)}<small>{formatTime(fact.observed_at)}</small></td>
              <td>{displayText(fact.source_agent)}<small>{Math.round(fact.confidence_score * 100)}% confidence</small></td>
              <td>{fact.superseded_by ? "Superseded" : "Active"}</td>
            </tr>
          ))}
        </tbody></table></div>
      ) : <p className="empty-detail">No ledger records have been returned.</p>}
    </InspectorCard>
  );
}

export default App;
