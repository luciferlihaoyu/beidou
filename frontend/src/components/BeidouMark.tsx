export default function BeidouMark({
  className = "h-6 w-6",
  color = "currentColor",
}: {
  className?: string;
  color?: string;
}) {
  // 北斗七星：四星成斗，三星为柄
  const stars: [number, number][] = [
    [13, 16], // 天枢
    [10, 28], // 天璇
    [26, 32], // 天玑
    [30, 21], // 天权
    [38, 18], // 玉衡
    [47, 13], // 开阳
    [58, 8], // 摇光
  ];
  const lines: [number, number][] = [
    [0, 1],
    [1, 2],
    [2, 3],
    [3, 0],
    [3, 4],
    [4, 5],
    [5, 6],
  ];
  return (
    <svg viewBox="0 0 64 40" className={className} fill="none" aria-label="北斗">
      {lines.map(([a, b], i) => (
        <line
          key={i}
          x1={stars[a][0]}
          y1={stars[a][1]}
          x2={stars[b][0]}
          y2={stars[b][1]}
          stroke={color}
          strokeWidth="1"
          opacity="0.35"
        />
      ))}
      {stars.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={i === 3 ? 2 : 2.6} fill={color} />
      ))}
    </svg>
  );
}
