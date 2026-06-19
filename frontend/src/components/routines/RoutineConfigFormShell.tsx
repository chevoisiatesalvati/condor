import type { RoutineFieldInfo } from "@/lib/api";
import { GroupedRoutineConfigForm } from "@/components/routines/GroupedRoutineConfigForm";
import { RoutineConfigForm } from "@/components/routines/RoutineConfigForm";

interface Props {
  fields: Record<string, RoutineFieldInfo>;
  groups?: string[];
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}

export function RoutineConfigFormShell({
  fields,
  groups,
  values,
  onChange,
}: Props) {
  if (groups && groups.length > 0) {
    return (
      <GroupedRoutineConfigForm
        fields={fields}
        groups={groups}
        values={values}
        onChange={onChange}
      />
    );
  }
  return <RoutineConfigForm fields={fields} values={values} onChange={onChange} />;
}
