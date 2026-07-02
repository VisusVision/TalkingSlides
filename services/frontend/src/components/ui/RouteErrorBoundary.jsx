import React from 'react';
import SurfaceCard from './SurfaceCard';
import Button from './Button';
import { I18nContext } from '../../i18n/I18nProvider';

export default class RouteErrorBoundary extends React.Component {
  static contextType = I18nContext;

  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidUpdate(previousProps) {
    if (this.state.error && previousProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }
    const { t } = this.context;

    return (
      <SurfaceCard elevated className="mx-auto max-w-2xl space-y-4 text-center" role="alert">
        <p className="label-sm">{t('errors.pageError')}</p>
        <h1 className="headline-md text-[var(--text-primary)]">{t('errors.pageCouldNotRender')}</h1>
        <p className="body-md">
          {t('errors.pageCouldNotRenderBody')}
        </p>
        <div className="flex flex-wrap justify-center gap-3">
          <Button type="button" onClick={() => window.location.reload()}>
            <span>{t('common.reload')}</span>
          </Button>
          <Button type="button" variant="secondary" onClick={() => window.location.assign('/')}>
            <span>{t('common.dashboard')}</span>
          </Button>
        </div>
      </SurfaceCard>
    );
  }
}
