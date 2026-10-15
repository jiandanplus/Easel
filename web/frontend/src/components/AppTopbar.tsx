import { useState } from 'react';
import { NAV_GROUPS } from './navigation';
import type { Page } from './Sidebar';
import type { PersonaItem } from '../lib/api';
import { IconSearch, IconProfile } from './icons';
import ThemeToggle from './ThemeToggle';

interface Props {
  currentPage: Page;
  settingsOpen: boolean;
  personas: PersonaItem[];
  selectedPersona: string;
  onNavigate: (page: Page) => void;
  onPersonaChange: (name: string) => void;
  onNewProfile: () => void;
  onAppearance: () => void;
}

export default function AppTopbar(props: Props) {
  const [query, setQuery] = useState('');
  const items = NAV_GROUPS.flatMap(group => group.items);
  const selected = items.find(item => item.page === props.currentPage);
  const matches = query.trim() ? items.filter(item => item.label.includes(query.trim())) : [];
  const navigate = (page: Page) => { props.onNavigate(page); setQuery(''); };
  return <header className="app-topbar">
    <div className="topbar-breadcrumb"><span>创作空间</span><span className="breadcrumb-divider">/</span><strong>{props.settingsOpen ? '设置' : selected?.label}</strong></div>
    <div className="topbar-search">
      <IconSearch size={17} />
      <input aria-label="搜索功能页面" placeholder="搜索功能页面" value={query}
        onChange={event => setQuery(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Escape') setQuery('');
          if (event.key === 'Enter' && matches[0]) navigate(matches[0].page);
        }} />
      {query.trim() && <div className="topbar-search-results" aria-label="搜索结果">
        {matches.length ? matches.map(({ page, label, Icon }) => <button key={page} onClick={() => navigate(page)}><Icon size={17} />{label}<span>前往 ↗</span></button>) : <p>没有匹配的页面</p>}
      </div>}
    </div>
    <ThemeToggle onClick={props.onAppearance} />
    <div className="topbar-persona">
      <span className="persona-avatar" aria-hidden="true"><IconProfile size={18} /></span>
      <select aria-label="当前创作画像" value={props.selectedPersona} onChange={event => {
        if (event.target.value === '__new__') props.onNewProfile();
        else props.onPersonaChange(event.target.value);
      }}>
        <option value="">通用模式</option>
        {props.personas.map(persona => <option key={persona.name} value={persona.name}>{persona.name}</option>)}
        <option value="__new__">＋ 新建画像</option>
      </select>
    </div>
  </header>;
}
