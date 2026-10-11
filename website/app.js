/* ═══════════════════════════════════════════════════════════════════════════
   TEAM77 • CYBER-TACTICAL COCKPIT CONTROLLER
   100% Integrated with Discord Bot & ProjectRPG Database
   ═══════════════════════════════════════════════════════════════════════════ */

// ─── API BASE & STAN ───
const API_BASE = (window.location.protocol === 'file:')
    ? (localStorage.getItem('team77_api_host') || 'http://localhost:8080')
    : '';

const state = {
    authToken: localStorage.getItem('team77_token') || '',
    isLoggedIn: false,
    allApplications: [],
    appFilter: 'all',
    appSearch: '',
    feeFilterUnpaidOnly: false,
    embedFields: [],
    radioPlaying: false,
    allMembers: [],
    allChannels: []
};

// ─── TOAST NOTIFICATIONS ───
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = (type === 'success' ? '⚡ ' : '⚠️ ') + message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-10px)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ─── MODAL HELPERS ───
function openModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('active');
}

function closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active');
}

// ─── NAWIGACJA ZAKŁADEK COCKPIT ───
function switchTab(tabName, subTab = null) {
    document.querySelectorAll('.dock-nav-item, .nav-tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

    const btn = document.querySelector(`.dock-nav-item[data-tab="${tabName}"], .nav-tab-btn[data-tab="${tabName}"]`);
    if (btn) btn.classList.add('active');

    const pane = document.getElementById(`pane-${tabName}`);
    if (pane) pane.classList.add('active');

    if (tabName === 'treasury') {
        loadDonations();
        loadFeeCheck();
        if (subTab === 'fee') {
            switchTreasurySubTab('fee');
        } else {
            switchTreasurySubTab('donations');
        }
    } else if (tabName === 'recruitment') {
        loadApplications();
    } else if (tabName === 'discipline') {
        loadStrikes();
        loadGuildMembers();
    } else if (tabName === 'announcements') {
        loadChannels();
        updateAnnouncementPreview();
    } else if (tabName === 'leaderboards') {
        loadLeaderboards();
    }

    window.scrollTo({ top: 0, behavior: 'smooth' });
}

document.querySelectorAll('.dock-nav-item, .nav-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        if (tab) switchTab(tab);
    });
});

function switchTreasurySubTab(type) {
    const btnDonations = document.getElementById('btn-treasury-donations-tab');
    const btnFee = document.getElementById('btn-treasury-fee-tab');
    const viewDonations = document.getElementById('subview-donations');
    const viewFee = document.getElementById('subview-fee');

    if (type === 'fee') {
        if (btnDonations) { btnDonations.className = 'btn-cyber-secondary'; }
        if (btnFee) { btnFee.className = 'btn-cyber-primary'; }
        if (viewDonations) viewDonations.style.display = 'none';
        if (viewFee) viewFee.style.display = 'block';
        loadFeeCheck();
    } else {
        if (btnDonations) { btnDonations.className = 'btn-cyber-primary'; }
        if (btnFee) { btnFee.className = 'btn-cyber-secondary'; }
        if (viewDonations) viewDonations.style.display = 'block';
        if (viewFee) viewFee.style.display = 'none';
        loadDonations();
    }
}

// ─── KOPIOWANIE IP SERWERA MTA ───
function copyMTAIP() {
    const ip = 'mta.projectrpg.pl:22003';
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(ip).then(() => {
            const lbl = document.getElementById('copy-ip-label');
            const icon = document.getElementById('copy-ip-icon');
            if (lbl) lbl.textContent = 'Skopiowano!';
            if (icon) icon.textContent = '✅';
            showToast('Skopiowano IP serwera MTA: ' + ip, 'success');
            setTimeout(() => {
                if (lbl) lbl.textContent = 'Kopiuj IP';
                if (icon) icon.textContent = '📋';
            }, 3000);
        }).catch(() => {
            showToast('Adres serwera MTA: ' + ip, 'success');
        });
    } else {
        showToast('Adres serwera MTA: ' + ip, 'success');
    }
}

// ─── LIVE RADIO PROJECT FM PLAYER ENGINE ───
const radioAudio = document.getElementById('radio-audio-element');
const btnDeckPlay = document.getElementById('btn-deck-play');
const deckEqualizer = document.getElementById('deck-equalizer');
const deckVolSlider = document.getElementById('deck-vol-slider');

if (btnDeckPlay && radioAudio) {
    btnDeckPlay.addEventListener('click', toggleRadioPlayback);
}

function toggleRadioPlayback() {
    if (!radioAudio) return;
    if (state.radioPlaying) {
        radioAudio.pause();
        radioAudio.src = '';
        state.radioPlaying = false;
        if (btnDeckPlay) btnDeckPlay.textContent = '▶';
        if (deckEqualizer) deckEqualizer.classList.remove('playing');
        showToast('Wstrzymano odtwarzanie Radia Project FM.', 'success');
    } else {
        radioAudio.src = 'https://panelradiowy.projectrpg.pl/listen/projectfm/main.mp3';
        radioAudio.play().then(() => {
            state.radioPlaying = true;
            if (btnDeckPlay) btnDeckPlay.textContent = '⏸';
            if (deckEqualizer) deckEqualizer.classList.add('playing');
            showToast('Odtwarzanie Radia Project FM On-Air! 📻', 'success');
        }).catch(err => {
            console.error('Radio play err:', err);
            state.radioPlaying = false;
            if (btnDeckPlay) btnDeckPlay.textContent = '▶';
            if (deckEqualizer) deckEqualizer.classList.remove('playing');
            showToast('Nie udało się uruchomić strumienia w przeglądarce.', 'error');
        });
    }
}

if (deckVolSlider && radioAudio) {
    deckVolSlider.addEventListener('input', (e) => {
        radioAudio.volume = parseFloat(e.target.value);
    });
}

// ─── AUTORYZACJA ZARZĄDU (CLEARANCE LEVEL) ───
const authModal = document.getElementById('auth-modal');
const btnAuth = document.getElementById('btn-auth-modal');
const modalPass = document.getElementById('modal-password');

if (btnAuth) {
    btnAuth.addEventListener('click', () => {
        if (state.isLoggedIn) {
            state.authToken = '';
            state.isLoggedIn = false;
            localStorage.removeItem('team77_token');
            updateAuthUI();
            showToast('Wylogowano z panelu zarządu.', 'success');
        } else {
            openModal('auth-modal');
            if (modalPass) {
                modalPass.value = '';
                modalPass.focus();
            }
        }
    });
}

document.getElementById('btn-login-submit')?.addEventListener('click', handleLogin);
modalPass?.addEventListener('keydown', e => { if (e.key === 'Enter') handleLogin(); });

async function handleLogin() {
    const pass = modalPass?.value.trim() || '';
    if (!pass) return;

    try {
        const res = await fetch(`${API_BASE}/api/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pass })
        });
        const data = await res.json();
        if (data.success && data.token) {
            state.authToken = data.token;
            state.isLoggedIn = true;
            localStorage.setItem('team77_token', data.token);
            closeModal('auth-modal');
            updateAuthUI();
            showToast('Dostęp ZARZĄD ODBLOKOWANY! 🔓', 'success');
            loadDonations();
            loadChannels();
            loadApplications();
            loadFeeCheck();
            loadStrikes();
        } else {
            showToast(data.error || 'Błędne hasło administracyjne!', 'error');
        }
    } catch (err) {
        showToast('Błąd połączenia z serwerem bota. Upewnij się, że bot działa.', 'error');
    }
}

function updateAuthUI() {
    const clearanceCard = document.getElementById('clearance-card');
    const clearanceLevelText = document.getElementById('clearance-level-text');

    if (state.isLoggedIn) {
        if (btnAuth) btnAuth.textContent = '🔓';
        if (clearanceLevelText) clearanceLevelText.innerHTML = '🔓 Zarząd (Aktywny)';
        if (clearanceCard) clearanceCard.classList.add('unlocked');
    } else {
        if (btnAuth) btnAuth.textContent = '🔑';
        if (clearanceLevelText) clearanceLevelText.innerHTML = '🔒 Gość (Podgląd)';
        if (clearanceCard) clearanceCard.classList.remove('unlocked');
    }
}

// ─── POMOCNICZE DOM ───
function setElText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

// ─── POBIERANIE STATUSU BOTA & TELEMETRII ───
async function fetchStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/status`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // Ukryj banner błędu
        const offlineAlert = document.getElementById('offline-alert');
        if (offlineAlert) offlineAlert.style.display = 'none';

        // Badge w headerze
        const badge = document.getElementById('bot-status-badge');
        if (badge) {
            badge.textContent = `● Bot Online (${data.bot.latency_ms} ms)`;
            badge.className = 'badge-status badge-online';
        }

        // Telemetria w Docku
        setElText('dock-ping', `${data.bot.latency_ms} ms`);
        const dockLed = document.getElementById('dock-led');
        if (dockLed) dockLed.style.background = 'var(--neon-green)';

        // Uptime
        const upH = Math.floor(data.bot.uptime_seconds / 3600);
        const upM = Math.floor((data.bot.uptime_seconds % 3600) / 60);
        setElText('hdr-uptime', `${upH}h ${upM}m`);
        setElText('stat-uptime', `Uptime bota: ${upH}h ${upM}m`);

        // MTA Server Telemetry
        const mtaText = data.mta.online ? `ONLINE (${data.mta.players}/${data.mta.max_players})` : 'OFFLINE';
        setElText('hdr-mta-status', mtaText);
        setElText('stat-mta-players', `${data.mta.players} / ${data.mta.max_players}`);
        setElText('stat-mta-ping', `Ping: ${data.mta.ping} ms • ${data.mta.online ? 'Aktywny' : 'Offline'}`);

        const mtaBadge = document.getElementById('mta-online-badge');
        if (mtaBadge) {
            mtaBadge.textContent = data.mta.online ? '● Online' : '● Offline';
            mtaBadge.className = data.mta.online ? 'pill-status pill-approved' : 'pill-status pill-rejected';
        }

        const mtaFill = document.getElementById('mta-progress-fill');
        if (mtaFill) {
            const pct = Math.min(100, Math.round((data.mta.players / (data.mta.max_players || 500)) * 100));
            mtaFill.style.width = `${pct}%`;
        }

        // Statystyki Overview
        setElText('stat-treasury', `${Number(data.treasury.total_collected).toLocaleString()} PLN`);
        setElText('stat-pending-donations', `Oczekujące wpłaty: ${data.treasury.pending_count}`);
        setElText('stat-pending-donations-num', data.treasury.pending_count || 0);

        setElText('stat-unpaid-fees-num', (data.treasury && data.treasury.unpaid_fees_count) || 0);
        setElText('stat-pending-apps-num', (data.recruitment && data.recruitment.pending_count) || 0);

        setElText('stat-members', data.community.total_members);
        setElText('stat-members-detail', `Aktywni na VC: ${data.community.voice_active} • Online: ${data.community.online_members}`);

        // Quick badges & Dock counters
        setElText('quick-badge-donations', `${data.treasury.pending_count || 0} Oczekujących`);
        setElText('nav-badge-donations', data.treasury.pending_count || 0);

        setElText('quick-badge-fee', `${(data.treasury && data.treasury.unpaid_fees_count) || 0} Do opłacenia`);
        setElText('quick-badge-apps', `${(data.recruitment && data.recruitment.pending_count) || 0} Nowych podań`);
        setElText('nav-badge-apps', (data.recruitment && data.recruitment.pending_count) || 0);

        // Radio Status
        if (data.radio) {
            if (data.radio.current) {
                const showTitle = data.radio.current.name || 'ProjectFM Pasmo Muzyczne';
                const hostName = data.radio.current.host || 'System Auto-DJ';
                setElText('deck-show-title', showTitle);
                setElText('deck-presenter-name', hostName);
                setElText('radio-cur-title', showTitle);
                setElText('radio-cur-host', `Prezenter: ${hostName}`);

                let timeStr = '⏱️ Trwa audycja na żywo';
                if (data.radio.current.dateStart && data.radio.current.dateEnd) {
                    const s = data.radio.current.dateStart.split('T')[1].slice(0, 5);
                    const e = data.radio.current.dateEnd.split('T')[1].slice(0, 5);
                    timeStr = `⏱️ ${s} — ${e} (czas antenowy)`;
                }
                setElText('radio-cur-time', timeStr);
            } else {
                setElText('deck-show-title', 'Project FM • Pasmo 24/7');
                setElText('deck-presenter-name', 'System Auto-DJ');
                setElText('radio-cur-title', 'ProjectFM Pasmo Muzyczne');
                setElText('radio-cur-host', 'Prezenter: System Auto-DJ');
                setElText('radio-cur-time', '⏱️ Pasmo 24/7');
            }

            if (data.radio.next) {
                setElText('radio-next-title', `${data.radio.next.name} (${data.radio.next.host || 'DJ'})`);
                let nextStart = data.radio.next.dateStart ? data.radio.next.dateStart.split('T')[1].slice(0, 5) : '--:--';
                setElText('radio-next-time', nextStart);
            } else {
                setElText('radio-next-title', 'Brak kolejnej audycji w ramówce');
                setElText('radio-next-time', '--:--');
            }

            const botLivePill = document.getElementById('radio-bot-live-pill');
            const voiceInfo = document.getElementById('radio-discord-voice-info');
            if (data.radio.bot_streaming) {
                if (botLivePill) {
                    botLivePill.textContent = `Discord: 🔊 #${data.radio.bot_channel || 'głosowy'}`;
                    botLivePill.className = 'pill-status pill-approved';
                }
                if (voiceInfo) voiceInfo.innerHTML = `🔊 Bot nadaje na Discordzie: <strong style="color: var(--cyan);">#${data.radio.bot_channel || 'głosowy'}</strong>`;
            } else {
                if (botLivePill) {
                    botLivePill.textContent = 'Discord: Nieaktywny';
                    botLivePill.className = 'pill-status pill-rejected';
                }
                if (voiceInfo) voiceInfo.innerHTML = `🔊 Transmisja na Discordzie: <em>Nie transmituje</em>`;
            }
        }

    } catch (err) {
        console.error('Błąd statusu:', err);
        const offlineAlert = document.getElementById('offline-alert');
        if (offlineAlert) offlineAlert.style.display = 'block';

        const badge = document.getElementById('bot-status-badge');
        if (badge) {
            badge.textContent = '● Bot Offline';
            badge.className = 'badge-status badge-offline';
        }
        const dockLed = document.getElementById('dock-led');
        if (dockLed) dockLed.style.background = 'var(--crimson)';
    }
}

// ─── SKARBIEC & WPŁATY ───
async function loadDonations() {
    const tbody = document.getElementById('donations-table-body');
    if (!tbody) return;

    if (!state.authToken) {
        tbody.innerHTML = `
            <tr><td colspan="8" style="text-align: center; color: var(--text-dim); padding: 30px;">
                🔒 Wpłaty są widoczne po zalogowaniu do panelu zarządu. Kliknij ikonę klucza 🔑 w lewym dolnym rogu.
            </td></tr>`;
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/donations`, {
            headers: { 'Authorization': `Bearer ${state.authToken}` }
        });
        if (res.status === 401) {
            state.isLoggedIn = false;
            updateAuthUI();
            return;
        }
        const data = await res.json();
        if (!data.donations || !data.donations.length) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-dim); padding: 25px;">Brak wpłat w bazie danych.</td></tr>';
            return;
        }

        tbody.innerHTML = data.donations.map(d => `
            <tr>
                <td style="font-family: var(--font-mono); color: var(--text-dim);">#${d.id}</td>
                <td style="font-weight: 700; color: #fff;">${escapeHtml(d.name)}</td>
                <td style="font-family: var(--font-mono); font-weight: 800; color: var(--neon-green);">${Number(d.amount).toLocaleString()} PLN</td>
                <td style="text-transform: capitalize; font-size: 0.85rem;">${escapeHtml(d.type)}</td>
                <td style="font-size: 0.82rem; color: var(--text-dim);">${d.created_at ? d.created_at.split(' ')[0] : '-'}</td>
                <td>
                    ${d.proof_url ? `<button type="button" class="btn-cyber-secondary" style="padding: 3px 8px; font-size: 0.75rem;" onclick="viewProof('${d.proof_url}')">🖼️ Zobacz</button>` : '<span style="color: var(--text-muted);">-</span>'}
                </td>
                <td>
                    <span class="pill-status ${d.status === 'approved' ? 'pill-approved' : (d.status === 'rejected' ? 'pill-rejected' : 'pill-pending')}">
                        ${d.status}
                    </span>
                </td>
                <td>
                    ${d.status === 'pending' ? `
                        <div style="display: flex; gap: 6px;">
                            <button class="btn-cyber-success" onclick="decideDonation(${d.id}, 'approved')">Zatwierdź</button>
                            <button class="btn-cyber-danger" onclick="decideDonation(${d.id}, 'rejected')">Odrzuć</button>
                        </div>
                    ` : '<span style="color: var(--text-muted); font-size: 0.8rem;">Rozpatrzono</span>'}
                </td>
            </tr>
        `).join('');
    } catch (err) {
        console.error(err);
        tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--crimson); padding: 20px;">Błąd pobierania wpłat.</td></tr>';
    }
}

async function decideDonation(id, action) {
    const isApprove = (action === 'approved');
    let reason = '';
    if (!isApprove) {
        reason = prompt('Podaj powód odrzucenia wpłaty (zostanie wysłany w DM użytkownikowi):');
        if (reason === null) return;
    } else {
        if (!confirm(`Czy na pewno chcesz ZATWIERDZIĆ wpłatę #${id} i wysłać embed na Discorda?`)) return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/donations/${id}/decision`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${state.authToken}`
            },
            body: JSON.stringify({ action, reason })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Wpłata #${id} została ${isApprove ? 'zatwierdzona' : 'odrzucona'} i zsynchronizowana z Discordem! 🚀`, 'success');
            loadDonations();
            fetchStatus();
        } else {
            showToast(data.error || 'Błąd zapisu decyzji.', 'error');
        }
    } catch (err) {
        showToast('Błąd połączenia z serwerem.', 'error');
    }
}

function viewProof(url) {
    const img = document.getElementById('proof-modal-img');
    if (img) img.src = url;
    openModal('proof-modal');
}

// ─── RADAR SKŁADEK (FEE CHECK) ───
async function loadFeeCheck() {
    const tbody = document.getElementById('fee-check-table-body');
    if (!tbody) return;

    if (!state.authToken) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-dim); padding: 30px;">
            🔒 Wymagana autoryzacja zarządu do wglądu w składki.
        </td></tr>`;
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/fee-check`, {
            headers: { 'Authorization': `Bearer ${state.authToken}` }
        });
        const data = await res.json();
        let members = data.members || [];

        if (state.feeFilterUnpaidOnly) {
            members = members.filter(m => !m.paid);
        }

        if (!members.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-dim); padding: 25px;">Brak wpisów spełniających kryteria.</td></tr>';
            return;
        }

        tbody.innerHTML = members.map(m => `
            <tr>
                <td>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <img src="${m.avatar || 'logo.png'}" style="width: 28px; height: 28px; border-radius: 50%;" onerror="this.src='zdj.png'">
                        <div>
                            <div style="font-weight: 700; color: #fff;">${escapeHtml(m.name)}</div>
                            <div style="font-size: 0.75rem; color: var(--text-dim);">@${escapeHtml(m.username)}</div>
                        </div>
                    </div>
                </td>
                <td style="font-family: var(--font-mono); font-weight: 700; color: var(--cyan);">${m.pid ? `PID: ${m.pid}` : '-'}</td>
                <td>
                    <span class="pill-status ${m.paid ? 'pill-approved' : 'pill-rejected'}">
                        ${m.paid ? 'Opłacona 🟢' : 'Zaległość 🔴'}
                    </span>
                </td>
                <td style="font-family: var(--font-mono); font-weight: 800; color: var(--neon-green);">${Number(m.amount_paid).toLocaleString()} PLN</td>
                <td style="font-size: 0.82rem; color: var(--text-dim);">${m.last_paid_at ? m.last_paid_at.split(' ')[0] : 'Nigdy'}</td>
                <td>
                    <div style="display: flex; gap: 6px;">
                        ${!m.paid ? `
                            <button class="btn-cyber-success" onclick="markFeePaid('${m.id}')">Oznacz Opłacone</button>
                            <button class="btn-cyber-danger" onclick="pingDiscordFeeMember('${m.id}', '${escapeHtml(m.name)}')">🔔 Ping Discord</button>
                        ` : '<span style="color: var(--neon-green); font-size: 0.8rem;">Rozliczony</span>'}
                    </div>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--crimson); padding: 20px;">Błąd pobierania bazy składek.</td></tr>';
    }
}

function toggleFeeFilter() {
    state.feeFilterUnpaidOnly = !state.feeFilterUnpaidOnly;
    const btn = document.getElementById('btn-fee-filter-unpaid');
    if (btn) {
        btn.textContent = state.feeFilterUnpaidOnly ? 'Pokaż wszystkich członków' : 'Pokaż tylko zalegających';
        btn.className = state.feeFilterUnpaidOnly ? 'btn-cyber-primary' : 'btn-cyber-secondary';
    }
    loadFeeCheck();
}

async function markFeePaid(memberId) {
    if (!confirm('Czy chcesz ręcznie zatwierdzić opłacenie składki członkowskiej dla tego użytkownika?')) return;
    try {
        const res = await fetch(`${API_BASE}/api/fee-check/mark-paid`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${state.authToken}`
            },
            body: JSON.stringify({ member_id: memberId })
        });
        const data = await res.json();
        if (data.success) {
            showToast('Zaktualizowano status składki!', 'success');
            loadFeeCheck();
            fetchStatus();
        } else {
            showToast(data.error || 'Błąd aktualizacji.', 'error');
        }
    } catch (err) {
        showToast('Błąd sieciowy.', 'error');
    }
}

async function pingDiscordFeeMember(memberId, name) {
    if (!confirm(`Czy wysłać oficjalne powiadomienie Discord do gracza ${name} o zaległej składce?`)) return;
    try {
        const res = await fetch(`${API_BASE}/api/fee-check/remind`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${state.authToken}`
            },
            body: JSON.stringify({ member_id: memberId })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Wysłano powiadomienie Discord do ${name}! 🔔`, 'success');
        } else {
            showToast(data.error || 'Błąd wysyłania powiadomienia.', 'error');
        }
    } catch (err) {
        showToast('Błąd wysyłania powiadomienia.', 'error');
    }
}

// ─── PIPELINE REKRUTACJI (APPLICATIONS) ───
async function loadApplications() {
    const tbody = document.getElementById('applications-table-body');
    if (!tbody) return;

    if (!state.authToken) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-dim); padding: 30px;">
            🔒 Wymagana autoryzacja zarządu do wglądu w podania.
        </td></tr>`;
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/applications`, {
            headers: { 'Authorization': `Bearer ${state.authToken}` }
        });
        const data = await res.json();
        state.allApplications = data.applications || [];

        // Aktualizacja liczników badge
        const allCount = state.allApplications.length;
        const pendingCount = state.allApplications.filter(a => (a.status || '').toLowerCase() === 'pending').length;
        const approvedCount = state.allApplications.filter(a => ['approved', 'accept', 'accepted'].includes((a.status || '').toLowerCase())).length;
        const rejectedCount = state.allApplications.filter(a => ['rejected', 'reject'].includes((a.status || '').toLowerCase())).length;

        const bAll = document.getElementById('badge-count-all');
        const bPen = document.getElementById('badge-count-pending');
        const bApp = document.getElementById('badge-count-approved');
        const bRej = document.getElementById('badge-count-rejected');
        if (bAll) bAll.textContent = allCount ? `(${allCount})` : '';
        if (bPen) bPen.textContent = pendingCount ? `(${pendingCount})` : '';
        if (bApp) bApp.textContent = approvedCount ? `(${approvedCount})` : '';
        if (bRej) bRej.textContent = rejectedCount ? `(${rejectedCount})` : '';

        renderApplicationsTable();
    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--crimson); padding: 20px;">Błąd pobierania podań.</td></tr>';
    }
}

function filterApplications(type) {
    state.appFilter = type;
    document.querySelectorAll('#pane-recruitment .btn-cyber-secondary').forEach(b => {
        b.classList.remove('active');
        b.style.borderColor = '';
        b.style.color = '';
    });
    const activeBtn = document.querySelector(`#pane-recruitment button[onclick*="${type}"]`);
    if (activeBtn) {
        activeBtn.classList.add('active');
        activeBtn.style.borderColor = 'var(--cyan)';
        activeBtn.style.color = 'var(--cyan)';
    }
    renderApplicationsTable();
}

function filterApplicationsSearch(query) {
    state.appSearch = (query || '').toLowerCase().trim();
    renderApplicationsTable();
}

function renderApplicationsTable() {
    const tbody = document.getElementById('applications-table-body');
    if (!tbody) return;

    let apps = state.allApplications;
    if (state.appFilter === 'pending') {
        apps = apps.filter(a => (a.status || '').toLowerCase() === 'pending');
    } else if (state.appFilter === 'approved') {
        apps = apps.filter(a => ['approved', 'accept', 'accepted'].includes((a.status || '').toLowerCase()));
    } else if (state.appFilter === 'rejected') {
        apps = apps.filter(a => ['rejected', 'reject'].includes((a.status || '').toLowerCase()));
    }

    if (state.appSearch) {
        apps = apps.filter(a => {
            const text = [
                a.id,
                a.name,
                a.discord_name,
                a.nick,
                a.login,
                a.uid,
                a.pid,
                a.user_id
            ].filter(Boolean).join(' ').toLowerCase();
            return text.includes(state.appSearch);
        });
    }

    if (!apps.length) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-dim); padding: 25px;">Brak podań spełniających wybrane kryteria.</td></tr>';
        return;
    }

    tbody.innerHTML = apps.map(a => {
        const candidateName = a.name || a.discord_name || a.nick || a.login || ('Kandydat #' + a.id);
        const sub = (a.nick && a.nick !== candidateName) ? a.nick : (a.login ? `Login: ${a.login}` : (a.user_id ? `ID: ${a.user_id}` : ''));
        const st = (a.status || 'pending').toLowerCase();
        const isApp = (st === 'accept' || st === 'approved' || st === 'accepted');
        const isRej = (st === 'reject' || st === 'rejected');
        const statusClass = isApp ? 'pill-approved' : (isRej ? 'pill-rejected' : 'pill-pending');
        const statusText = isApp ? 'Zaakceptowane' : (isRej ? 'Odrzucone' : 'Oczekuje');

        return `
            <tr>
                <td style="font-family: var(--font-mono); color: var(--text-dim);">#${a.id}</td>
                <td>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <img src="${a.avatar || 'logo.png'}" style="width: 28px; height: 28px; border-radius: 50%; object-fit: cover;" onerror="this.src='zdj.png'">
                        <div>
                            <div style="font-weight: 700; color: #fff;">${escapeHtml(candidateName)}</div>
                            ${sub ? `<div style="font-size: 0.74rem; color: var(--text-dim);">${escapeHtml(sub)}</div>` : ''}
                        </div>
                    </div>
                </td>
                <td style="font-family: var(--font-mono); font-weight: 700; color: var(--cyan);">${escapeHtml(a.uid || a.pid || '-')}</td>
                <td>${escapeHtml(a.age ? a.age + ' lat' : '-')}</td>
                <td>${escapeHtml(a.rp_points || a.hours || '0')} RP</td>
                <td>
                    <span class="pill-status ${statusClass}">
                        ${statusText}
                    </span>
                </td>
                <td style="font-size: 0.82rem; color: var(--text-dim);">${a.created_at ? a.created_at.split(' ')[0] : '-'}</td>
                <td style="text-align: right;">
                    <div style="display: flex; gap: 6px; justify-content: flex-end;">
                        <button class="btn-cyber-secondary" style="padding: 4px 10px; font-size: 0.78rem;" onclick="openAppDetail(${a.id})">
                            🔍 Rozpatrz
                        </button>
                        <button class="btn-cyber-danger" style="padding: 4px 8px; font-size: 0.78rem; border-color: rgba(255, 59, 48, 0.4); background: rgba(255, 59, 48, 0.1);" title="Usuń podanie z bazy" onclick="deleteApplication(${a.id})">
                            🗑️
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function openAppDetail(id) {
    const app = state.allApplications.find(a => a.id === id);
    if (!app) return;

    const modalTitle = document.getElementById('app-modal-title');
    const modalContent = document.getElementById('app-modal-content');
    const modalActions = document.getElementById('app-modal-actions');

    const candidateName = app.name || app.discord_name || app.nick || app.login || ('Kandydat #' + app.id);
    if (modalTitle) modalTitle.textContent = `📋 Podanie #${app.id} • ${candidateName}`;

    const st = (app.status || 'pending').toLowerCase();
    const isApp = (st === 'accept' || st === 'approved' || st === 'accepted');
    const isRej = (st === 'reject' || st === 'rejected');
    const statusClass = isApp ? 'pill-approved' : (isRej ? 'pill-rejected' : 'pill-pending');
    const statusText = isApp ? 'Zaakceptowane' : (isRej ? 'Odrzucone' : 'Oczekuje');

    if (modalContent) {
        modalContent.innerHTML = `
            <div style="background: rgba(0,0,0,0.4); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 14px; margin-bottom: 16px;">
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; font-size: 0.85rem;">
                    <div><strong>UID / PID:</strong> <span style="color: var(--cyan); font-family: var(--font-mono);">${escapeHtml(app.uid || app.pid || '-')}</span></div>
                    <div><strong>Wiek:</strong> ${escapeHtml(app.age || '-')} lat</div>
                    <div><strong>Ilość RP:</strong> ${escapeHtml(app.rp_points || app.hours || '0')}</div>
                    <div><strong>Status:</strong> <span class="pill-status ${statusClass}">${statusText}</span></div>
                </div>
                ${app.decision_reason ? `
                    <div style="margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--border-subtle); font-size: 0.84rem; color: #ff9999;">
                        <strong>Powód decyzji:</strong> ${escapeHtml(app.decision_reason)}
                    </div>
                ` : ''}
            </div>

            <div style="display: flex; flex-direction: column; gap: 10px;">
                <div style="background: rgba(255,255,255,0.02); border-left: 3px solid var(--cyan); padding: 10px 14px; border-radius: 4px;">
                    <div style="font-size: 0.72rem; font-weight: 700; color: var(--cyan); text-transform: uppercase;">Nick i Login w grze</div>
                    <div style="font-size: 0.9rem; color: #fff; margin-top: 3px;">Nick: <strong>${escapeHtml(app.nick || '-')}</strong> | Login: <strong>${escapeHtml(app.login || '-')}</strong></div>
                </div>

                <div style="background: rgba(255,255,255,0.02); border-left: 3px solid var(--cyan); padding: 10px 14px; border-radius: 4px;">
                    <div style="font-size: 0.72rem; font-weight: 700; color: var(--cyan); text-transform: uppercase;">Kto polecił Ci organizację?</div>
                    <div style="font-size: 0.9rem; color: #fff; margin-top: 3px;">${escapeHtml(app.recommended_by || 'Brak polecenia')}</div>
                </div>

                <div style="background: rgba(255,255,255,0.02); border-left: 3px solid var(--cyan); padding: 10px 14px; border-radius: 4px;">
                    <div style="font-size: 0.72rem; font-weight: 700; color: var(--cyan); text-transform: uppercase;">Zgoda na regulamin organizacji</div>
                    <div style="font-size: 0.9rem; color: #fff; margin-top: 3px;">${escapeHtml(app.rules_agreed || 'Tak')}</div>
                </div>

                <div style="background: rgba(255,255,255,0.02); border-left: 3px solid var(--cyan); padding: 10px 14px; border-radius: 4px;">
                    <div style="font-size: 0.72rem; font-weight: 700; color: var(--cyan); text-transform: uppercase;">Zgoda na wpłatę wpisowego (3 000 PLN)</div>
                    <div style="font-size: 0.9rem; color: #fff; margin-top: 3px;">${escapeHtml(app.fee_agreed || 'Tak')}</div>
                </div>

                ${app.forum_url ? `
                    <div style="background: rgba(255,255,255,0.02); border-left: 3px solid var(--purple); padding: 10px 14px; border-radius: 4px;">
                        <div style="font-size: 0.72rem; font-weight: 700; color: var(--purple); text-transform: uppercase;">Profil na forum ProjectRPG</div>
                        <div style="font-size: 0.9rem; margin-top: 3px;">
                            <a href="${app.forum_url}" target="_blank" style="color: var(--cyan); text-decoration: underline;">🔗 Otwórz profil kandydata na forum ProjectRPG</a>
                        </div>
                    </div>
                ` : ''}
            </div>
        `;
    }

    if (modalActions) {
        if (!isApp && !isRej) {
            modalActions.innerHTML = `
                <button class="btn-cyber-danger" style="background: rgba(255, 59, 48, 0.15); border-color: var(--crimson);" onclick="deleteApplication(${app.id})">🗑️ Usuń z Bazy</button>
                <button class="btn-cyber-danger" onclick="decideApplication(${app.id}, 'reject')">❌ Odrzuć z Powodem</button>
                <button class="btn-cyber-primary" onclick="decideApplication(${app.id}, 'accept')">✅ Zaakceptuj & Dodaj Rangę</button>
            `;
        } else {
            modalActions.innerHTML = `
                <button class="btn-cyber-danger" style="background: rgba(255, 59, 48, 0.15); border-color: var(--crimson);" onclick="deleteApplication(${app.id})">🗑️ Usuń z Bazy</button>
                <button class="btn-cyber-secondary" onclick="closeModal('app-modal')">Zamknij</button>
            `;
        }
    }

    openModal('app-modal');
}

async function decideApplication(id, action) {
    const isApprove = (action === 'accept' || action === 'approved');
    const act = isApprove ? 'accept' : 'reject';
    let reason = '';
    if (!isApprove) {
        reason = prompt('Podaj powód odrzucenia podania (zostanie wysłany w wiadomości DM do kandydata):');
        if (reason === null) return;
    } else {
        if (!confirm('Czy na pewno chcesz zaakceptować kandydata? Bot automatycznie nada mu rangę członka na Discordzie i wyśle wiadomość DM z zaproszeniem.')) return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/applications/${id}/decision`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${state.authToken}`
            },
            body: JSON.stringify({ action: act, reason })
        });
        const data = await res.json();
        if (data.success) {
            closeModal('app-modal');
            showToast(`Podanie #${id} zostało ${isApprove ? 'zaakceptowane' : 'odrzucone'}! 🚀`, 'success');
            loadApplications();
            fetchStatus();
        } else {
            showToast(data.error || 'Błąd podejmowania decyzji.', 'error');
        }
    } catch (err) {
        showToast('Błąd sieciowy.', 'error');
    }
}

async function deleteApplication(id) {
    if (!confirm(`Czy na pewno chcesz BEZPOWROTNIE USUNĄĆ podanie #${id} z bazy danych?\n(Wiadomość na kanale Discorda również zostanie zaktualizowana)`)) return;
    try {
        const res = await fetch(`${API_BASE}/api/applications/${id}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${state.authToken}`
            }
        });
        const data = await res.json();
        if (data.success) {
            closeModal('app-modal');
            showToast(`Podanie #${id} zostało trwale usunięte z bazy! 🗑️`, 'success');
            loadApplications();
            fetchStatus();
        } else {
            showToast(data.error || 'Błąd usuwania podania.', 'error');
        }
    } catch (err) {
        showToast('Błąd sieciowy podczas usuwania podania.', 'error');
    }
}

// ─── SKANER PROJECTRPG ───
document.getElementById('lookup-query-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') runPlayerLookup(); });

async function runPlayerLookup() {
    const query = document.getElementById('lookup-query-input')?.value.trim();
    if (!query) {
        showToast('Wprowadź nick, PID lub serial gracza.', 'error');
        return;
    }

    const resCard = document.getElementById('scanner-result');
    if (resCard) resCard.style.display = 'none';

    try {
        showToast('Przeszukiwanie bazy ProjectRPG...', 'success');
        const res = await fetch(`${API_BASE}/api/projectrpg/lookup?query=${encodeURIComponent(query)}`);
        const data = await res.json();

        if (!data.found) {
            showToast('Nie odnaleziono gracza o podanych kryteriach na serwerze.', 'error');
            return;
        }

        const forum = data.forum || {};
        const game = data.game_info || {};

        setElText('scanner-nick', forum.nick || game.nick || query);
        setElText('scanner-pid', `PID: ${forum.uid || game.uid || '-'}`);
        setElText('scanner-org', game.org ? `${game.org} (${game.org_rank || 'Członek'})` : 'Brak organizacji');
        setElText('scanner-hours', `${game.hours || 0} godz.`);
        setElText('scanner-pp', `${game.rp_points || 0} PP`);
        setElText('scanner-licenses', game.licenses || 'A, B, C');
        setElText('scanner-penalties', `${game.penalty_points || 0} aktywnych`);

        const avatarImg = document.getElementById('scanner-avatar');
        if (avatarImg) {
            avatarImg.src = forum.avatar_url || 'logo.png';
        }

        if (resCard) resCard.style.display = 'block';
        showToast('Skaner pobrał pełną metrykę gracza!', 'success');

    } catch (err) {
        showToast('Błąd modułu skanera bazy ProjectRPG.', 'error');
    }
}

// ─── DYSCYPLINA (TRYBUNAŁ & KARY) ───
async function loadStrikes() {
    const tbody = document.getElementById('strikes-table-body');
    if (!tbody) return;

    if (!state.authToken) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-dim); padding: 30px;">
            🔒 Wymagana autoryzacja zarządu do wglądu w kartotekę kar.
        </td></tr>`;
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/strikes`, {
            headers: { 'Authorization': `Bearer ${state.authToken}` }
        });
        const data = await res.json();
        const all = [...(data.strikes || []), ...(data.warnings || [])];

        if (!all.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-dim); padding: 25px;">Brak zarejestrowanych kar w rejestrze.</td></tr>';
            return;
        }

        tbody.innerHTML = all.map(s => `
            <tr>
                <td style="font-weight: 700; color: #fff;">${escapeHtml(s.member_name)}</td>
                <td>
                    <span class="pill-status ${s.type === 'warning' ? 'pill-pending' : 'pill-rejected'}">
                        ${s.type === 'warning' ? '⚠️ Ostrzeżenie' : '🛑 Minus / Nagana'}
                    </span>
                </td>
                <td style="color: var(--text-main); font-size: 0.85rem;">${escapeHtml(s.reason)}</td>
                <td style="font-size: 0.82rem; color: var(--text-dim);">${escapeHtml(s.admin_name)}</td>
                <td style="font-size: 0.82rem; color: var(--text-dim);">${s.created_at ? s.created_at.split(' ')[0] : '-'}</td>
                <td>
                    <button class="btn-cyber-danger" style="padding: 3px 8px; font-size: 0.75rem;" onclick="deletePenalty(${s.id}, '${s.type}')">Anuluj</button>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--crimson); padding: 20px;">Błąd pobierania kartoteki kar.</td></tr>';
    }
}

async function loadGuildMembers() {
    try {
        const res = await fetch(`${API_BASE}/api/guild/members`);
        const data = await res.json();
        state.allMembers = data.members || [];
        const sel = document.getElementById('strike-member-select');
        if (sel && state.allMembers.length) {
            sel.innerHTML = '<option value="">Wybierz członka organizacji...</option>' + state.allMembers.map(m => `
                <option value="${m.id}">${escapeHtml(m.name)} (@${escapeHtml(m.username)})</option>
            `).join('');
        }
    } catch (err) { }
}

async function submitStrike() {
    const memberId = document.getElementById('strike-member-select')?.value;
    const type = document.getElementById('strike-type-select')?.value;
    const reason = document.getElementById('strike-reason-input')?.value.trim();

    if (!memberId) {
        showToast('Wybierz członka organizacji do ukarania.', 'error');
        return;
    }
    if (!reason) {
        showToast('Wpisz powód nałożenia sankcji.', 'error');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/strikes/add`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${state.authToken}`
            },
            body: JSON.stringify({ member_id: memberId, type, reason })
        });
        const data = await res.json();
        if (data.success) {
            showToast('Sankcja została nałożona i opublikowana na kanale kar Discorda! ⚖️', 'success');
            document.getElementById('strike-reason-input').value = '';
            loadStrikes();
        } else {
            showToast(data.error || 'Błąd nakładania kary.', 'error');
        }
    } catch (err) {
        showToast('Błąd sieciowy podczas dodawania kary.', 'error');
    }
}

async function deletePenalty(id, type) {
    if (!confirm('Czy na pewno chcesz anulować tę karę dyscyplinarną?')) return;
    try {
        const res = await fetch(`${API_BASE}/api/strikes/${id}/delete?type=${type}`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${state.authToken}` }
        });
        const data = await res.json();
        if (data.success) {
            showToast('Kara została pomyślnie anulowana.', 'success');
            loadStrikes();
        } else {
            showToast(data.error || 'Nie udało się anulować kary.', 'error');
        }
    } catch (err) {
        showToast('Błąd sieciowy.', 'error');
    }
}

// ─── BROADCAST STUDIO 2.0 (OGŁOSZENIA) ───
async function loadChannels() {
    try {
        const res = await fetch(`${API_BASE}/api/channels`, {
            headers: { 'Authorization': `Bearer ${state.authToken}` }
        });
        const data = await res.json();
        state.allChannels = data.channels || [];

        const annSel = document.getElementById('announcement-channel-select');
        if (annSel && state.allChannels.length) {
            annSel.innerHTML = '<option value="">Wybierz kanał ogłoszeń...</option>' + state.allChannels.map(c => `
                <option value="${c.id}">${c.category} ➔ #${c.name}</option>
            `).join('');
        }
    } catch (err) { }
}

// Live preview binding
['announcement-title-input', 'announcement-desc-input', 'announcement-color-input', 'announcement-image-input', 'announcement-mention-select', 'announcement-channel-select'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', updateAnnouncementPreview);
    document.getElementById(id)?.addEventListener('change', updateAnnouncementPreview);
});

function updateAnnouncementPreview() {
    const title = document.getElementById('announcement-title-input')?.value || '📢 Oficjalny Komunikat Organizacji';
    const content = document.getElementById('announcement-desc-input')?.value || 'Wpisz treść komunikatu w edytorze obok...';
    const color = document.getElementById('announcement-color-input')?.value || '#00f0ff';
    const mention = document.getElementById('announcement-mention-select')?.value || 'none';
    const image = document.getElementById('announcement-image-input')?.value || '';

    // Channel name preview
    const channelSelect = document.getElementById('announcement-channel-select');
    const selectedOption = channelSelect?.selectedOptions[0];
    const previewChan = document.getElementById('preview-channel-name');
    if (previewChan) {
        previewChan.textContent = selectedOption && selectedOption.value ? selectedOption.text.split('➔')[1]?.replace('#', '').trim() || 'ogloszenia' : 'ogloszenia';
    }

    // Mention preview
    const prevMention = document.getElementById('preview-mention-text');
    if (prevMention) {
        if (mention === 'everyone') {
            prevMention.textContent = '@everyone';
            prevMention.style.display = 'block';
        } else if (mention === 'here') {
            prevMention.textContent = '@here';
            prevMention.style.display = 'block';
        } else {
            prevMention.style.display = 'none';
        }
    }

    // Embed styling
    const embedBox = document.getElementById('preview-embed-box');
    if (embedBox) embedBox.style.borderLeftColor = color;

    setElText('preview-embed-title', title);
    const prevDesc = document.getElementById('preview-embed-desc');
    if (prevDesc) prevDesc.textContent = content;

    const imgBox = document.getElementById('preview-embed-image-box');
    const imgEl = document.getElementById('preview-embed-image');
    if (imgBox && imgEl) {
        if (image) {
            imgEl.src = image;
            imgBox.style.display = 'block';
        } else {
            imgBox.style.display = 'none';
        }
    }
}

async function sendAnnouncement() {
    const channelId = document.getElementById('announcement-channel-select')?.value;
    const title = document.getElementById('announcement-title-input')?.value.trim();
    const content = document.getElementById('announcement-desc-input')?.value.trim();
    const color = document.getElementById('announcement-color-input')?.value || '#00f0ff';
    const mention = document.getElementById('announcement-mention-select')?.value || 'none';
    const image = document.getElementById('announcement-image-input')?.value.trim();

    if (!channelId) {
        showToast('Wybierz docelowy kanał tekstowy na serwerze Discord.', 'error');
        return;
    }
    if (!title || !content) {
        showToast('Wypełnij tytuł oraz treść ogłoszenia.', 'error');
        return;
    }

    if (!confirm('Czy na pewno chcesz opublikować ten komunikat na oficjalnym kanale Discorda?')) return;

    try {
        const payload = {
            channel_id: channelId,
            mention: mention,
            title: title,
            description: content,
            color: color,
            image_url: image || null,
            timestamp: true
        };

        const res = await fetch(`${API_BASE}/api/announcement`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${state.authToken}`
            },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
            showToast('Ogłoszenie zostało natychmiast wysłane na Discorda! 🚀', 'success');
        } else {
            showToast(data.error || 'Błąd publikacji na serwerze.', 'error');
        }
    } catch (err) {
        showToast('Błąd sieciowy.', 'error');
    }
}

// ─── RANKINGI & LEADERBOARDS ───
async function loadLeaderboards() {
    try {
        const res = await fetch(`${API_BASE}/api/leaderboards`);
        const data = await res.json();

        // 1. Donatorzy Skarbca
        const dBody = document.getElementById('top-donors-table-body');
        if (dBody) {
            if (!data.top_donors || !data.top_donors.length) {
                dBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-dim); padding: 25px;">Brak zarejestrowanych darczyńców.</td></tr>';
            } else {
                dBody.innerHTML = data.top_donors.map((d, i) => `
                    <tr>
                        <td style="font-family: var(--font-mono); font-weight: 800; font-size: 1rem;">
                            ${i === 0 ? '🥇 #1' : (i === 1 ? '🥈 #2' : (i === 2 ? '🥉 #3' : `#${i + 1}`))}
                        </td>
                        <td style="font-weight: 700; color: #fff;">${escapeHtml(d.name)}</td>
                        <td style="font-family: var(--font-mono); font-weight: 800; color: var(--neon-green);">${Number(d.amount).toLocaleString()} PLN</td>
                        <td style="font-size: 0.85rem; color: var(--text-dim);">${d.count || 1} wpłat</td>
                    </tr>
                `).join('');
            }
        }

        // 2. Aktywność Głosowa
        const vBody = document.getElementById('top-vc-table-body');
        if (vBody) {
            if (!data.top_voice || !data.top_voice.length) {
                vBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-dim); padding: 25px;">Brak danych aktywności VC.</td></tr>';
            } else {
                vBody.innerHTML = data.top_voice.map((v, i) => `
                    <tr>
                        <td style="font-family: var(--font-mono); font-weight: 800; font-size: 1rem;">
                            ${i === 0 ? '🥇 #1' : (i === 1 ? '🥈 #2' : (i === 2 ? '🥉 #3' : `#${i + 1}`))}
                        </td>
                        <td style="font-weight: 700; color: #fff;">${escapeHtml(v.name)}</td>
                        <td style="font-family: var(--font-mono); font-weight: 800; color: var(--cyan);">${escapeHtml(v.formatted)}</td>
                        <td style="font-size: 0.82rem; color: var(--text-dim);">Aktywny</td>
                    </tr>
                `).join('');
            }
        }
    } catch (err) { }
}

function escapeHtml(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// ─── PRZYCISK QUICK REFRESH ───
document.getElementById('btn-quick-refresh')?.addEventListener('click', () => {
    showToast('Odświeżanie danych bota i serwera...', 'success');
    fetchStatus();
    loadDonations();
    loadFeeCheck();
    loadApplications();
    loadLeaderboards();
});

// ─── START & INICJALIZACJA ───
if (state.authToken) {
    state.isLoggedIn = true;
    updateAuthUI();
    loadDonations();
    loadApplications();
    loadFeeCheck();
    loadStrikes();
    loadChannels();
}

fetchStatus();
loadLeaderboards();
updateAnnouncementPreview();
setInterval(fetchStatus, 8000);
