import { createContext, useContext } from "react";
import type { Character, Foreshadowing, WorldviewEntry } from "@/lib/api";

export interface ReferenceData {
  characters: Character[];
  settings: WorldviewEntry[];
  foreshadows: Foreshadowing[];
}

const ReferenceDataContext = createContext<ReferenceData | null>(null);

export function ReferenceDataProvider({
  value,
  children,
}: {
  value: ReferenceData;
  children: React.ReactNode;
}) {
  return <ReferenceDataContext.Provider value={value}>{children}</ReferenceDataContext.Provider>;
}

export function useReferenceData(): ReferenceData {
  // 默认值：全空（用于 Editor 顶层尚未加载时——节点视图仍可渲染 chip，
  // 浮卡会显示"设置中尚无此条目"）
  return useContext(ReferenceDataContext) ?? { characters: [], settings: [], foreshadows: [] };
}
