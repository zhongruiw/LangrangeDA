import numpy as np
# from numba import jit
from scipy import sparse
from mode_truc import truncate, inv_truncate

''' 
Lagrangian DA 
    1. using tracers to recover the two-layer flow field 
    2. the reference (true) flow field is from QG model
    3. the reduced-order model is complex OU process / nonlinear conditional Gaussian model
'''

def get_A_OU(x,y,K,r1,r2,KX,KY):
    '''
    1. The mode truncation should be done outside this function. 
    2. K is the original number of modes in each x and y direction. 
    3. x, y should be of shape (N,L) for proper broadcasting.
    '''
    N, L = x.shape
    KX_flat = KX
    KY_flat = KY
    E = np.zeros((N, 2*L, KX_flat.shape[0]), dtype=np.complex_)

    exp_term = np.exp(1j * (x[:, :, None] @ KX_flat[None,:] + y[:, :, None] @ KY_flat[None,:]))
    E[:, :L,:] = exp_term * (1j) * KY_flat
    E[:, L:,:] = exp_term * (-1j) * KX_flat
    # R_bot = sparse.hstack([sparse.diags(r1.flatten(order='F')), sparse.diags(r2.flatten(order='F'))], format="csr")
    R_bot = np.hstack((np.diag(r1), np.diag(r2)))
    A = E @ R_bot / K**2

    return A


def get_A_CG(K, KX, KY, k_index_map, kd, beta, kappa, nu, U, psi1_hat, h_hat):
    '''
    1. The mode truncation should be done outside this function. 
    2. K is the original number of modes in each x and y direction. 
    3. psi_hat, hk should be of shape (N,k_left) for proper broadcasting.
    '''
    N, k_left = psi1_hat.shape
    
    # Precompute constants
    K_squared = KX**2 + KY**2
    K_squared_kd2 = K_squared + kd**2 / 2
    K_squared2 = K_squared**2
    K_squared4 = K_squared**4
    invCk = K_squared * (K_squared + kd**2)
    dX = 1j * KX
    list_dic_k_map = list(k_index_map.keys())

    # linear part for A0, a0, A1 and a1
    linear_A0 = dX * ((K_squared_kd2 * beta - K_squared2 * U) * psi1_hat - kd**2/2 * U * h_hat) - nu * K_squared4 * (invCk * psi1_hat - kd**2/2 * h_hat)
    linear_a0 = dX * ((kd**2/2 * beta - kd**2 * K_squared * U) * psi1_hat - K_squared_kd2 * U * h_hat) + nu * K_squared4 * K_squared_kd2 * h_hat
    linear_A1_diag = dX * (kd**2/2 * beta + kd**2 * K_squared * U) - (kd**2/2 * kappa * K_squared)
    linear_a1_diag = dX * (K_squared_kd2 * beta + K_squared2 * U) - K_squared_kd2 * kappa * K_squared - nu * K_squared4 * invCk
    linear_A1 = np.tile(np.diag(linear_A1_diag)[None,:,:], (N,1,1)) 
    linear_a1 = np.tile(np.diag(linear_a1_diag)[None,:,:], (N,1,1)) 
    
    # nonlinear summation part for A0, a0, A1 and a1
    nonlinear_sum_A0 = np.zeros_like(psi1_hat, dtype=complex)
    nonlinear_sum_a0 = np.zeros_like(psi1_hat, dtype=complex)
    nonlinear_sum_A1 = np.zeros_like(linear_A1, dtype=complex)
    nonlinear_sum_a1 = np.zeros_like(linear_a1, dtype=complex)
    
    for ik_, (k, ik) in enumerate(k_index_map.items()):
        kx, ky = k
        ikx, iky = ik
        k_sq = kx**2 + ky**2
        for im_, (m, im) in enumerate(k_index_map.items()):
            mx, my = m
            imx, imy = im
            m_sq = mx**2 + my**2
            psi1_m = psi1_hat[:, im_]
            n = (kx-mx, ky-my)
            if n in k_index_map:
                in_ = list_dic_k_map.index(n)
                psi1_n = psi1_hat[:, in_]
                h_n = h_hat[in_]
                det_mn = np.linalg.det(np.array([m, n]))
                nonlinear_sum_A0[:, ik_] -= det_mn * ((k_sq + kd**2/2) * (m_sq + kd**2/2) * psi1_n*psi1_m)
                nonlinear_sum_a0[:, ik_] -= det_mn * (kd**2/2 * (m_sq + kd**2/2) * psi1_n*psi1_m)
                nonlinear_sum_A1[:, ik_, im_] += det_mn * kd**2/2 * (k_sq * psi1_n - h_n)
                nonlinear_sum_a1[:, ik_, im_] -= det_mn * (k_sq * kd**2/2 * psi1_n + (k_sq + kd**2/2) * h_n) 
        
    nonlinear_sum_A0 = nonlinear_sum_A0 / K**2
    nonlinear_sum_a0 = nonlinear_sum_a0 / K**2
    nonlinear_sum_A1 = nonlinear_sum_A1 / K**2
    nonlinear_sum_a1 = nonlinear_sum_a1 / K**2
    
    # aggregate 
    A0 = linear_A0 + nonlinear_sum_A0
    a0 = linear_a0 + nonlinear_sum_a0
    A1 = linear_A1 + nonlinear_sum_A1
    a1 = linear_a1 + nonlinear_sum_a1
    
    # normalization 
    Ck = 1 / invCk
    Ck[K_squared == 0] = 0  # avoid division by zero at k = 0, constant mode
    Ck_ = np.tile(Ck[None,:,None], (N,1,k_left))
    A0 = Ck * A0
    a0 = Ck * a0
    A1 = Ck_ * A1
    a1 = Ck_ * a1
    
    return A0, a0, A1, a1


def forward_OU(N, N_chunk, K, dt, x, y, r1, r2, mu0, a0, a1, R0, InvBoB, Sigma_u, mu_t, R_t, KX, KY):
    # leverage the diagonal matrix property for acceleration
    a1_diag = a1.diagonal()
    Sigma_u_diag = Sigma_u.diagonal()

    for i in range(1, N):
        i_chunk = (i-1) % N_chunk
        if i_chunk == 0:
            A1_t = get_A_OU(x[i-1:i-1+N_chunk, :], y[i-1:i-1+N_chunk, :], K, r1, r2, KX, KY)
        
        x0 = x[i - 1, :]
        y0 = y[i - 1, :]
        x1 = x[i, :]
        y1 = y[i, :]
        x_diff = np.mod(x1 - x0 + np.pi, 2 * np.pi) - np.pi # consider periodic boundary conditions
        y_diff = np.mod(y1 - y0 + np.pi, 2 * np.pi) - np.pi # consider periodic boundary conditions

        # precompute
        A1 = A1_t[i_chunk, :, :]
        R0_A1_H = R0 @ A1.conj().T
        Sigma_u_diag2 = Sigma_u_diag * np.conj(Sigma_u_diag)

        # Update the posterior mean and posterior covariance
        mu = mu0 + (a0 + a1 @ mu0) * dt + R0_A1_H * InvBoB @ (np.hstack((x_diff, y_diff)) - A1 @ mu0 * dt)
        R = R0 + (a1_diag[:,None] * R0 + R0 * a1_diag.conj() + np.diag(Sigma_u_diag2) - R0_A1_H * InvBoB @ R0_A1_H.conj().T) * dt
        mu_t[:, i] = mu
        R_t[:, i] = np.diag(R)
        mu0 = mu
        R0 = R

    return mu_t, R_t


def forward_CG(N, N_chunk, dt, K, KX_cut, KY_cut, k_index_map_cut, mu0, R0, InvBoB, sigma_2, mu_t, R_t, kd, beta, kappa, nu, U, psi1_k_t_cut_T, h_k_cut):
    for i in range(1, N):
        i_chunk = (i-1) % N_chunk
        if i_chunk == 0:
            A0_t, a0_t, A1_t, a1_t= get_A_CG(K, KX_cut, KY_cut, k_index_map_cut, kd, beta, kappa, nu, U, psi1_k_t_cut_T[i-1:i-1+N_chunk,:], h_k_cut)
        
        A0 = A0_t[i_chunk, :]
        a0 = a0_t[i_chunk, :]
        A1 = A1_t[i_chunk, :, :]
        a1 = a1_t[i_chunk, :, :]

        # precompute
        a1R0 = a1 @ R0
        sigma_2_sq = sigma_2 * np.conjugate(sigma_2)
        R0A1_H = R0 @ A1.conj().T
        psi1_diff = psi1_k_t_cut_T[i, :] - psi1_k_t_cut_T[i-1, :]
        
        # Update the posterior mean and posterior covariance
        mu = mu0 + (a0 + a1 @ mu0) * dt + (R0 @ A1.conj().T) * InvBoB @ (psi1_diff - (A0 + A1 @ mu0) * dt)
        R = R0 + (a1R0 + a1R0.conj().T + np.diag(sigma_2_sq) - (R0A1_H) * InvBoB @ R0A1_H.conj().T) * dt
        mu_t[:, i] = mu
        R_t[:, i] = np.diag(R)
        mu0 = mu
        R0 = R

    return mu_t, R_t


def mu2psi(mu_t, K, r_cut, style):
    '''reshape flattened variables to two modes matrices'''
    mu_t_ = mu_t.reshape((mu_t.shape[0] // 2, 2, -1), order='F')
    psi_k = inv_truncate(mu_t_[:,0,:], r_cut, K, style)
    tau_k = inv_truncate(mu_t_[:,1,:], r_cut, K, style)
        
    return psi_k, tau_k
    

class Lagrangian_DA_OU:
    def __init__(self, N, N_chunk, K, psi_k_t, tau_k_t, r1, r2, dt, sigma_xy, f, gamma, omega, sigma, xt, yt, r_cut, style='circle'):
        """
        Parameters:
        - N: int, total number of steps
        - N_chunk: trunk for calculating DA coefficient matrix
        - K: number of Fourier modes along one axis
        - style: truncation style, 'circle' or 'square'
        - psi_k_t: np.array of shape (K, K, N), truth time series of the Fourier eigenmode1 stream function. only to provide DA initial condition
        - tau_k_t: np.array of shape (K, K, N), truth time series of the Fourier eigenmode2 stream function. only to provide DA initial condition
        - r1: eigenvectors1
        - r2: eigenvectors2
        - dt: float, time step
        - sigma_xy: float, standard deviation of the observation noise
        - f: forcing in complex OU process model
        - gamma: damping in complex OU process model
        - omega: phase in complex OU process model
        - sigma: noise standard deviation in complex OU process model
        - xt: observations x of shape (L, N)
        - yt: observations y of shape (L, N)
        - r_cut: modes truncation radius
        """
        self.N = N
        self.N_chunk = N_chunk
        self.K = K
        self.dt = dt
        self.style = style
        self.InvBoB = 1 / sigma_xy**2
        self.mu0 = np.concatenate((truncate(psi_k_t[:,:,0],r_cut, style=style), truncate(tau_k_t[:,:,0],r_cut, style=style))) # assume the initial condition is truth
        self.n = self.mu0.shape[0]
        self.R0 = np.zeros((self.n, self.n), dtype='complex')
        self.mu_t = np.zeros((self.n, N), dtype='complex')  # posterior mean
        self.mu_t[:, 0] = self.mu0
        self.R_t = np.zeros((self.n, N), dtype='complex')  # posterior covariance
        self.R_t[:, 0] = np.diag(self.R0)  # only save the diagonal elements
        self.a0 = truncate(f,r_cut, style=style).flatten(order='F')
        self.a1 = -np.diag(truncate(gamma,r_cut, style=style).flatten(order='F')) + 1j * np.diag(truncate(omega,r_cut, style=style).flatten(order='F'))
        self.Sigma_u = np.diag(truncate(sigma,r_cut, style=style).flatten(order='F'))
        self.x = xt
        self.y = yt
        self.r_cut = r_cut
        Kx = np.fft.fftfreq(K) * K
        Ky = np.fft.fftfreq(K) * K
        KX, KY = np.meshgrid(Kx, Ky)
        self.KX = truncate(KX, r_cut, style)
        self.KY = truncate(KY, r_cut, style)
        self.r1 = truncate(r1[:,:,0], r_cut, style)
        self.r2 = truncate(r2[:,:,0], r_cut, style)

    def forward(self):
        mu_t, R_t = forward_OU(self.N, self.N_chunk, self.K, self.dt, self.x, self.y, self.r1, self.r2, self.mu0, self.a0, self.a1, self.R0, self.InvBoB, self.Sigma_u, self.mu_t, self.R_t, self.KX, self.KY)

        return mu_t, R_t


class Lagrangian_DA_CG:
    def __init__(self, N, N_chunk, K, dt, psi1_k_t, psi2_k_t, sigma_1, sigma_2, kd, beta, kappa, nu, U, h_hat, r_cut, style='circle'):
        """
        Parameters:
        - N: int, total number of steps
        - N_chunk: trunk for calculating DA coefficient matrix
        - K: number of Fourier modes along one axis
        - style: truncation style, 'circle' or 'square'
        - psi1_k_t: np.array of shape (K, K, N), truth time series of the Fourier eigenmode1 stream function.
        - psi2_k_t: np.array of shape (K, K, N), only used for the DA initial condition, assumed to be truth 
        - dt: float, time step
        - sigma_xy: float, standard deviation of the observation noise
        - r_cut: modes truncation radius
        """
        self.N = N
        self.N_chunk = N_chunk
        self.K = K
        self.dt = dt
        Kx = np.fft.fftfreq(K) * K
        Ky = np.fft.fftfreq(K) * K
        KX, KY = np.meshgrid(Kx, Ky)
        self.KX_cut = truncate(KX, r_cut, style)
        self.KY_cut = truncate(KY, r_cut, style)
        self.k_index_map_cut = {(KX[iy, ix], KY[iy, ix]): (ix, iy) for ix in range(K) for iy in range(K) if (KX[iy, ix]**2 + KY[iy, ix]**2) <=r_cut**2}
        psi1_k_t_cut = truncate(psi1_k_t, r_cut, style)
        psi2_k_t_cut = truncate(psi2_k_t, r_cut, style)
        self.h_k_cut = truncate(h_hat, r_cut, style)
        self.psi1_k_t_cut_T = np.transpose(psi1_k_t_cut, axes=(1,0)) # psi_hat should be of shape shape (N,k_left)
        # initialized mean and variance
        self.mu0 = psi2_k_t_cut[:, 0] # assume the initial condition is truth
        K_ = psi2_k_t_cut.shape[0] # number of flattened K modes
        self.R0 = np.zeros((K_, K_), dtype='complex')
        self.mu_t = np.zeros((K_, N), dtype='complex')  # posterior mean
        self.mu_t[:, 0] = self.mu0
        self.R_t = np.zeros((K_, N), dtype='complex')  # posterior covariance
        self.R_t[:, 0] = np.diag(self.R0)  # only save the diagonal elements
        self.sigma_1 = sigma_1
        self.sigma_2 = sigma_2 * np.ones(K_)
        self.InvBoB = 1 / sigma_1**2
        self.kd = kd
        self.beta = beta
        self.kappa = kappa
        self.nu = nu
        self.U = U
        self.style = style
        self.r_cut = r_cut

    def forward(self):
        mu_t, R_t = forward_CG(self.N, self.N_chunk, self.dt, self.K, self.KX_cut, self.KY_cut, self.k_index_map_cut, self.mu0, self.R0, self.InvBoB, self.sigma_2, self.mu_t, self.R_t, self.kd, self.beta, self.kappa, self.nu, self.U, self.psi1_k_t_cut_T, self.h_k_cut)

        return mu_t, R_t