import type { ComponentType } from 'react';
import type { Page } from './Sidebar';
import { IconDashboard, IconChat, IconFire, IconIdea, IconCalendar, IconPublish, IconSkills, IconOutputs, IconAccounts, IconProfile } from './icons';

export const NAV_GROUPS: { label: string; items: { page: Page; Icon: ComponentType<{ size?: number }>; label: string }[] }[] = [
  { label: '创作', items: [
    { page: 'dashboard', Icon: IconDashboard, label: '工作台' },
    { page: 'chat', Icon: IconChat, label: '对话' },
    { page: 'trends', Icon: IconFire, label: '热点雷达' },
    { page: 'ideas', Icon: IconIdea, label: '选题库' },
    { page: 'breakdown', Icon: IconSkills, label: '爆款拆解' },
    { page: 'skills', Icon: IconSkills, label: '技能库' },
  ] },
  { label: '内容运营', items: [
    { page: 'outputs', Icon: IconOutputs, label: '内容库' },
    { page: 'calendar', Icon: IconCalendar, label: '内容日历' },
    { page: 'publish', Icon: IconPublish, label: '发布中心' },
  ] },
  { label: '管理', items: [
    { page: 'accounts', Icon: IconAccounts, label: '账号' },
    { page: 'profile', Icon: IconProfile, label: '画像' },
  ] },
];
