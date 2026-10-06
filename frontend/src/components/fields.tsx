import { useId } from 'react';

export const inputClass =
  'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100';

export function NumberField(props: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number | 'any';
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="text-sm">
      <label htmlFor={id} className="mb-1 block text-slate-400">
        {props.label}
      </label>
      <input
        id={id}
        type="number"
        className={inputClass}
        value={props.value}
        min={props.min}
        max={props.max}
        step={props.step ?? 1}
        aria-describedby={props.hint ? `${id}-hint` : undefined}
        onChange={(e) => {
          props.onChange(Number(e.target.value));
        }}
      />
      {props.hint && (
        <span id={`${id}-hint`} className="mt-1 block text-xs text-slate-500">
          {props.hint}
        </span>
      )}
    </div>
  );
}
