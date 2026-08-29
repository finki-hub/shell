import type { LabV2ServerMessage } from '@shell/protocol';

export type DeploymentWarningAlertMessage = Extract<
  LabV2ServerMessage,
  { readonly type: 'deployment-warning-alert' }
>;
export type DeploymentWarningClearedMessage = Extract<
  LabV2ServerMessage,
  { readonly type: 'deployment-warning-cleared' }
>;
export type DeploymentWarningMessage = Extract<
  LabV2ServerMessage,
  { readonly type: 'deployment-warning' }
>;
