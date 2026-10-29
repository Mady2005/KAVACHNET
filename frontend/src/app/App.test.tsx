import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe,it,expect} from 'vitest';
import App from './App';

describe('login',()=>{
  it('renders accessible sign-in controls after restoring the session',async()=>{
    render(<App/>);
    expect(await screen.findByLabelText(/email or username/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
  });
  it('validates required fields',async()=>{
    render(<App/>);
    const user=userEvent.setup();
    await screen.findByRole('button',{name:/sign in securely/i});
    await user.click(screen.getByRole('button',{name:/sign in securely/i}));
    expect(await screen.findByRole('alert')).toHaveTextContent(/enter your username/i);
  });
});
