import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'react-toastify';

import { useAuth } from '../hooks/useAuth';

const Login = () => {
  const [tenant, setTenant] = useState('tenant_1');
  const [userType, setUserType] = useState('user');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleLogin = async (event) => {
    event.preventDefault();
    setLoading(true);

    try {
      await login({ tenantId: tenant, role: userType });
      navigate('/dashboard');
      toast.success('Login successful!');
    } catch (error) {
      toast.error(`Login failed: ${error.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <h1 className="mt-6 text-center text-3xl font-extrabold text-gray-900">
          Enterprise Chat Agent
        </h1>
        <p className="mt-2 text-center text-sm text-gray-600">
          Multi-tenant chat system with AI support
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="bg-white py-8 px-4 shadow sm:rounded-lg sm:px-10">
          <form className="space-y-6" onSubmit={handleLogin}>
            <div>
              <label htmlFor="organization" className="block text-sm font-medium text-gray-700">
                Organization
              </label>
              <select
                id="organization"
                value={tenant}
                onChange={(event) => setTenant(event.target.value)}
                className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500"
              >
                <option value="tenant_1">Acme Corp</option>
                <option value="tenant_2">TechStart Inc</option>
              </select>
            </div>

            <div>
              <label htmlFor="user-type" className="block text-sm font-medium text-gray-700">
                User Type
              </label>
              <select
                id="user-type"
                value={userType}
                onChange={(event) => setUserType(event.target.value)}
                className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500"
              >
                <option value="user">Demo User</option>
                <option value="admin">Administrator</option>
                <option value="agent">Support Agent</option>
              </select>
            </div>

            <div className="bg-blue-50 border-l-4 border-blue-400 p-4">
              <p className="text-sm text-blue-700">
                <strong>Demo SSO Integration</strong>
                <br />
                In production, this would integrate with your organization&apos;s identity
                provider (SAML, OIDC, etc.).
              </p>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50"
            >
              {loading ? 'Authenticating...' : 'Sign In via SSO'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};

export default Login;
