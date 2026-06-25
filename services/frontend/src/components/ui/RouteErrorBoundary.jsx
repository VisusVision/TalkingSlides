import React from 'react';
import SurfaceCard from './SurfaceCard';
import Button from './Button';

export default class RouteErrorBoundary extends React.Component {
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

    return (
      <SurfaceCard elevated className="mx-auto max-w-2xl space-y-4 text-center" role="alert">
        <p className="label-sm">Page Error</p>
        <h1 className="headline-md text-[var(--text-primary)]">This page could not render</h1>
        <p className="body-md">
          The app kept the shell open, but this route hit a runtime error. Reload the page or return to the dashboard.
        </p>
        <div className="flex flex-wrap justify-center gap-3">
          <Button type="button" onClick={() => window.location.reload()}>
            <span>Reload</span>
          </Button>
          <Button type="button" variant="secondary" onClick={() => window.location.assign('/')}>
            <span>Dashboard</span>
          </Button>
        </div>
      </SurfaceCard>
    );
  }
}
